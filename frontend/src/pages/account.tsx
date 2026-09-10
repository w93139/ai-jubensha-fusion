import Link from 'next/link';
import AppLayout from '@/components/AppLayout';
import AuthGuard from '@/components/AuthGuard';
import { useAuthStore } from '@/stores/authStore';
export default function AccountPage() {
  const user = useAuthStore(state => state.user);
  return <AuthGuard><AppLayout><section className="mx-auto max-w-xl px-5 py-10"><h1 className="font-dossier text-3xl">个人信息</h1><div className="my-6 rounded-xl border border-line bg-panel p-5"><p className="text-sm text-mist">当前账号</p><p className="mt-2 break-words text-xl">{user?.nickname || user?.username}</p><p className="mt-5 text-sm leading-6 text-mist">在这个账号下开始的游戏，可以在“我的记录”中继续。</p></div><div className="flex flex-wrap gap-4"><Link className="min-h-11 rounded border border-brass/40 p-3 text-brass" href="/play/records">我的记录</Link><Link className="min-h-11 p-3 text-brass" href="/">返回首页</Link></div></section></AppLayout></AuthGuard>;
}
