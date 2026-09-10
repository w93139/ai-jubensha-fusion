import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { useAuthStore } from '@/stores/authStore';
import { Loader2 } from 'lucide-react';
import { authReturnPath } from '@/lib/authReturnPath';

interface ProtectedRouteProps {
  children: React.ReactNode;
  requireAuth?: boolean;
  redirectTo?: string;
}

const ProtectedRoute: React.FC<ProtectedRouteProps> = ({ 
  children, 
  requireAuth = true,
  redirectTo = '/auth/login'
}) => {
  const router = useRouter();
  const { isAuthenticated, isLoading } = useAuthStore();
  const [isChecking, setIsChecking] = useState(true);

  useEffect(() => {
    const checkAuth = async () => {
      // 等待认证状态加载完成
      if (isLoading || !router.isReady) {
        return;
      }

      setIsChecking(false);

      // 如果需要认证但用户未登录，重定向到登录页
      if (requireAuth && !isAuthenticated) {
        router.replace({ pathname: redirectTo, query: { returnUrl: authReturnPath(router.asPath) } });
        return;
      }

      // 登录后回到原入口，缺省进入剧本中心。
      if (!requireAuth && isAuthenticated && 
          (router.pathname.startsWith('/auth/') || router.pathname === '/auth')) {
        router.replace(authReturnPath(router.query.returnUrl));
        return;
      }
    };

    checkAuth();
  }, [isAuthenticated, isLoading, requireAuth, router, redirectTo]);

  // 显示加载状态
  if (isLoading || isChecking) {
    return (
      <div className="min-h-screen bg-ink flex items-center justify-center">
        <div className="text-center">
          <Loader2 className="h-8 w-8 animate-spin text-brass mx-auto mb-4" />
          <p className="text-mist">正在验证身份...</p>
        </div>
      </div>
    );
  }

  // 如果需要认证但用户未登录，不渲染内容（等待重定向）
  if (requireAuth && !isAuthenticated) {
    return null;
  }

  // 如果用户已登录但访问认证页面，不渲染内容（等待重定向）
  if (!requireAuth && isAuthenticated && 
      (router.pathname.startsWith('/auth/') || router.pathname === '/auth')) {
    return null;
  }

  return <>{children}</>;
};

export default ProtectedRoute;
