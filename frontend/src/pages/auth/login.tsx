import React, { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import { KeyRound, Send, Smile, Smartphone, Swords, Ticket } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useAuthStore } from '@/stores/authStore';
import { authService } from '@/services/authService';
import ProtectedRoute from '@/components/ProtectedRoute';
import StarField from '@/components/StarField';
import { toast } from 'sonner';

const PHONE_RE = /^1[3-9]\d{9}$/;

export default function LoginPage() {
  const router = useRouter();
  const { phoneLogin, isLoading, error, isAuthenticated, clearError } = useAuthStore();
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [inviteCode, setInviteCode] = useState('');
  const [nickname, setNickname] = useState('');
  const [sending, setSending] = useState(false);
  const [countdown, setCountdown] = useState(0);

  useEffect(() => {
    if (isAuthenticated) router.push('/script-center');
  }, [isAuthenticated, router]);

  useEffect(() => {
    if (!countdown) return;
    const timer = window.setInterval(() => setCountdown(value => Math.max(value - 1, 0)), 1000);
    return () => window.clearInterval(timer);
  }, [countdown]);

  useEffect(() => () => clearError(), [clearError]);

  const requestCode = async () => {
    if (!PHONE_RE.test(phone)) return toast.error('请输入有效的 11 位手机号');
    try {
      setSending(true);
      const result = await authService.sendSmsCode(phone);
      setCountdown(result.retry_after || 60);
      if (result.dev_code) {
        setCode(result.dev_code);
        toast.success(`本地测试验证码 ${result.dev_code}，已自动填入`);
      } else toast.success(result.message);
    } catch (requestError) {
      toast.error(requestError instanceof Error ? requestError.message : '验证码发送失败');
    } finally {
      setSending(false);
    }
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!PHONE_RE.test(phone)) return toast.error('请输入有效的 11 位手机号');
    if (!/^\d{6}$/.test(code)) return toast.error('请输入 6 位验证码');
    try {
      await phoneLogin({ phone, code, invite_code: inviteCode.trim() || undefined, nickname: nickname.trim() || undefined });
      toast.success('登录成功，欢迎来到人生海海');
      router.push('/script-center');
    } catch (loginError) {
      toast.error(loginError instanceof Error ? loginError.message : '登录失败');
    }
  };

  return (
    <ProtectedRoute requireAuth={false}>
      <div className="min-h-screen flex">
        <div className="hidden md:flex w-[45%] relative flex-col items-center justify-center overflow-hidden bg-gradient-to-b from-ink to-[#151A24] border-r border-hairline">
          <StarField />
          <div className="relative z-10 flex flex-col items-center text-center px-12 gap-6">
            <div className="h-16 w-16 rounded-sm bg-brass/10 border border-brass/40 flex items-center justify-center"><Swords className="h-8 w-8 text-brass" /></div>
            <h1 className="text-4xl font-dossier font-semibold text-paper">人生海海</h1>
            <div className="w-24 border-t border-thread/40" />
            <p className="text-mist text-lg leading-8">一人入局，众生皆戏。<br />用你的选择，驶向故事深处。</p>
          </div>
        </div>

        <div className="flex-1 flex items-center justify-center bg-gradient-to-b from-ink via-ink to-panel px-6 py-12">
          <div className="w-full max-w-md space-y-7">
            <div className="flex md:hidden items-center gap-2 justify-center"><Swords className="h-6 w-6 text-brass" /><span className="text-paper font-dossier font-semibold text-lg">人生海海</span></div>
            <div><h2 className="text-3xl font-dossier font-semibold text-paper">手机号登录</h2><p className="mt-2 text-mist text-sm">首次登录需邀请码，并设置你的游戏昵称</p></div>

            <form onSubmit={submit} className="space-y-5">
              <Field label="手机号" icon={<Smartphone className="h-4 w-4" />}>
                <Input value={phone} onChange={e => { setPhone(e.target.value.replace(/\D/g, '').slice(0, 11)); clearError(); }} inputMode="numeric" autoComplete="tel" placeholder="请输入 11 位手机号" className="pl-10" disabled={isLoading} />
              </Field>
              <Field label="手机验证码" icon={<KeyRound className="h-4 w-4" />}>
                <div className="flex gap-2"><Input value={code} onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} inputMode="numeric" autoComplete="one-time-code" placeholder="6 位验证码" className="pl-10" disabled={isLoading} /><Button type="button" variant="outline" className="shrink-0 border-brass/40 text-brass" onClick={requestCode} disabled={sending || countdown > 0}><Send className="mr-1 h-4 w-4" />{countdown > 0 ? `${countdown}s` : '获取验证码'}</Button></div>
              </Field>
              <Field label="邀请码（首次登录必填）" icon={<Ticket className="h-4 w-4" />}><Input value={inviteCode} onChange={e => setInviteCode(e.target.value)} placeholder="请输入邀请码" className="pl-10 uppercase" disabled={isLoading} /></Field>
              <Field label="自定义昵称（首次登录必填）" icon={<Smile className="h-4 w-4" />}><Input value={nickname} onChange={e => setNickname(e.target.value.slice(0, 50))} placeholder="你希望其他角色如何称呼你" className="pl-10" disabled={isLoading} /></Field>
              {error && <div className="text-thread text-sm bg-thread/10 border border-thread/30 rounded-sm px-3 py-2">{error}</div>}
              <Button type="submit" className="w-full bg-brass/10 border border-brass/40 text-brass hover:bg-brass/20 font-semibold" disabled={isLoading}>{isLoading ? '正在进入故事…' : '验证并进入'}</Button>
              <p className="text-center text-xs text-faint">未注册手机号验证成功后将自动创建账户</p>
            </form>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}

function Field({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
  return <div className="space-y-1.5"><Label className="text-mist text-sm">{label}</Label><div className="relative"><span className="absolute left-3 top-1/2 z-10 -translate-y-1/2 text-faint">{icon}</span>{children}</div></div>;
}
