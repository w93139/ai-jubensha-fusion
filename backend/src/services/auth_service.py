"""用户认证服务"""
import os
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional, Union
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from jose import JWTError, jwt  # type: ignore
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from src.db.models.user import User
from src.schemas.user_schemas import TokenData

# 新密码使用 Argon2；BcryptHasher 只用于兼容已有 bcrypt 密码。
password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))

# JWT配置
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 24 * 60* 30))

class AuthService:
    """认证服务类"""
    
    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        return password_hash.verify(plain_password, hashed_password)
    
    @staticmethod
    def get_password_hash(password: str) -> str:
        """获取密码哈希"""
        return password_hash.hash(password)
    
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """创建访问令牌"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def verify_token(token: str) -> TokenData:
        """验证令牌"""
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id: int = payload.get("user_id")  # type: ignore
            username: str = payload.get("sub")  # type: ignore
            
            if user_id is None or username is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无效的认证令牌",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            token_data = TokenData(user_id=user_id, username=username)
            return token_data
            
        except JWTError as e:
            if "expired" in str(e).lower():
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="令牌已过期",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="无效的认证令牌",
                    headers={"WWW-Authenticate": "Bearer"},
                )
    
    @staticmethod
    def get_user_from_token(db: Session, token: str) -> Optional[User]:
        """验证令牌并返回对应用户

        令牌无效时抛出 HTTPException（与 verify_token 一致）；
        令牌有效但用户不存在时返回 None。
        统一的令牌验证入口，供认证中间件和 WebSocket 端点复用。
        """
        token_data = AuthService.verify_token(token)
        if token_data.username is None:
            return None
        return AuthService.get_user_by_username(db, token_data.username)

    @staticmethod
    def authenticate_user(db: Session, username: str, password: str) -> Optional[User]:
        """认证用户"""
        # 支持用户名或邮箱登录
        user = db.query(User).filter(
            (User.username == username) | (User.email == username)
        ).first()
        
        if not user:
            return None
        # User.to_dict() intentionally excludes password hashes by default.
        # Authentication runs server-side and should read the mapped attribute
        # directly instead of weakening the model's safe serialization default.
        if not AuthService.verify_password(password, str(user.hashed_password)):
            return None
        
        return user
    
    @staticmethod
    def get_user_by_username(db: Session, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        return db.query(User).filter(User.username == username).first()
    
    @staticmethod
    def get_user_by_email(db: Session, email: str) -> Optional[User]:
        """根据邮箱获取用户"""
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def get_user_by_phone(db: Session, phone: str) -> Optional[User]:
        return db.query(User).filter(User.phone == phone).first()

    @staticmethod
    def send_sms_code(phone: str) -> dict:
        """Store a short-lived OTP in Redis. Mock mode returns the code to the local UI."""
        from redis import Redis
        from redis.exceptions import RedisError

        redis_client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        rate_key = f"auth:sms:rate:{phone}"
        code_key = f"auth:sms:code:{phone}"
        try:
            if redis_client.exists(rate_key):
                ttl = max(redis_client.ttl(rate_key), 1)
                raise HTTPException(status_code=429, detail=f"请 {ttl} 秒后再获取验证码")

            provider = os.getenv("SMS_PROVIDER", "mock").lower()
            if provider != "mock":
                raise HTTPException(status_code=503, detail="真实短信服务尚未配置")

            code = os.getenv("SMS_MOCK_CODE", "123456") or f"{secrets.randbelow(1000000):06d}"
            redis_client.setex(code_key, 300, code)
            redis_client.setex(rate_key, 60, "1")
            return {
                "message": "验证码已发送",
                "expires_in": 300,
                "retry_after": 60,
                "dev_code": code,
            }
        except HTTPException:
            raise
        except RedisError as exc:
            raise HTTPException(status_code=503, detail="验证码服务暂不可用") from exc

    @staticmethod
    def verify_sms_code(phone: str, code: str) -> None:
        from redis import Redis

        redis_client = Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            decode_responses=True,
        )
        key = f"auth:sms:code:{phone}"
        expected = redis_client.get(key)
        if not expected or not hmac.compare_digest(str(expected), code):
            raise HTTPException(status_code=400, detail="验证码错误或已过期")
        redis_client.delete(key)

    @staticmethod
    def authenticate_or_create_phone_user(
        db: Session,
        phone: str,
        code: str,
        invite_code: Optional[str] = None,
        nickname: Optional[str] = None,
    ) -> User:
        user = AuthService.get_user_by_phone(db, phone)
        if user:
            AuthService.verify_sms_code(phone, code)
            return user

        allowed_codes = {
            item.strip() for item in os.getenv("INVITE_CODES", "").split(",") if item.strip()
        }
        if not invite_code or invite_code.strip() not in allowed_codes:
            raise HTTPException(status_code=403, detail="邀请码无效")
        if not nickname or not nickname.strip():
            raise HTTPException(status_code=400, detail="首次登录请设置昵称")

        AuthService.verify_sms_code(phone, code)

        generated_password = secrets.token_urlsafe(32)
        user = User(
            username=f"u_{phone}",
            email=f"{phone}@phone.local",
            phone=phone,
            hashed_password=AuthService.get_password_hash(generated_password),
            nickname=nickname.strip(),
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user
    
    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
        """根据ID获取用户"""
        return db.query(User).filter(User.id == user_id).first()
    
    @staticmethod
    def create_user(db: Session, username: str, email: str, password: str, nickname: Optional[str] = None) -> User:
        """创建用户"""
        # 检查用户名是否已存在
        if AuthService.get_user_by_username(db, username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在"
            )
        
        # 检查邮箱是否已存在
        if AuthService.get_user_by_email(db, email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="邮箱已被注册"
            )
        
        # 创建新用户
        hashed_password = AuthService.get_password_hash(password)
        db_user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            nickname=nickname or username
        )
        
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        
        return db_user
    
    @staticmethod
    def update_user_profile(db: Session, user_id: int, **kwargs) -> User:
        """更新用户资料"""
        user = AuthService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户不存在"
            )
        
        # 更新允许的字段
        allowed_fields = ['nickname', 'bio', 'avatar_url']
        for field, value in kwargs.items():
            if field in allowed_fields and value is not None:
                setattr(user, field, value)
        
        db.commit()
        db.refresh(user)
        
        return user
    
    @staticmethod
    def change_password(db: Session, user_id: int, old_password: str, new_password: str) -> bool:
        """修改密码"""
        user = AuthService.get_user_by_id(db, user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户不存在"
            )
        
        # 验证旧密码
        if not AuthService.verify_password(old_password, str(user.hashed_password)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="旧密码错误"
            )
        
        # 更新密码
        user.set_hashed_password(AuthService.get_password_hash(new_password))
        db.commit()
        
        return True
    
    @staticmethod
    def update_last_login(db: Session, user_id: int) -> None:
        """更新最后登录时间"""
        user = AuthService.get_user_by_id(db, user_id)
        if user:
            setattr(user, 'last_login_at', datetime.utcnow())
            db.commit()

    @staticmethod
    def get_or_create_guest_user(db: Session, username: str, email: str) -> User:
        """获取或创建默认访客用户（用于匿名访问）"""
        user = AuthService.get_user_by_username(db, username)
        if user:
            return user

        # 生成随机密码（访客账户密码不对外暴露）
        import secrets
        random_password = secrets.token_urlsafe(32)
        hashed_password = AuthService.get_password_hash(random_password)

        db_user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            nickname="访客",
        )
        db.add(db_user)
        db.commit()
        db.refresh(db_user)
        return db_user
