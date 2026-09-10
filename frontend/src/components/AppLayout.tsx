import DockBar from '@/components/DockBar';
import { Button } from '@/components/ui/button';
import UserMenu from '@/components/UserMenu';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/authStore';
import {
  BookOpen, History, Plus, Menu,
  X
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/router';
import React, { useState } from 'react';

interface AppLayoutProps {
  children: React.ReactNode;
  showSidebar?: boolean;
  backgroundImage?: string;
  isGamePage?: boolean;
  gameWorkspace?: boolean;
}

const PAGE_TITLES: Record<string, string> = {
  '/': '首页',
  '/script-center': '首页',
  '/play': '首页',
  '/play/records': '我的记录',
  '/play/package-preview': '开始新游戏',
  '/account': '个人信息',
  '/game': '游戏',
  '/profile': '个人资料',
  '/profile/game-history': '游戏历史',
  '/profile/change-password': '设置',
  '/script-manager/create': '创建剧本',
  '/admin/source-bundles': '来源材料核验',
  '/admin/script-reviews': '剧本审核记录',
  '/admin/authoring-jobs': '编译与模型审核',
};

const getPageTitle = (pathname: string): string => {
  if (PAGE_TITLES[pathname]) return PAGE_TITLES[pathname];
  const matched = Object.keys(PAGE_TITLES).find(
    key => key !== '/' && pathname.startsWith(key)
  );
  return matched ? PAGE_TITLES[matched] : '';
};

// 移动端导航项
interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  requireAuth?: boolean;
}

const mobileNavItems: NavItem[] = [
  { href: '/', label: '首页', icon: BookOpen },
  { href: '/play/package-preview', label: '开始新游戏', icon: Plus, requireAuth: true },
  { href: '/play/records', label: '我的记录', icon: History, requireAuth: true },
];

const AppLayout: React.FC<AppLayoutProps> = ({ 
  children, 
  showSidebar = true,
  backgroundImage,
  isGamePage = false,
  gameWorkspace = false,

}) => {
  const router = useRouter();
  const { isAuthenticated } = useAuthStore();
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [dockExpanded, setDockExpanded] = useState(false);


  const filteredMobileNavItems = mobileNavItems.filter(item => 
    !item.requireAuth || isAuthenticated
  );

  return (
    <div onKeyDown={event => { if (sidebarOpen && event.key === 'Escape') { event.preventDefault(); setSidebarOpen(false); document.querySelector<HTMLButtonElement>('[aria-label="关闭导航"]')?.focus(); } }} className="app-shell min-h-screen bg-ink text-paper" data-game-workspace={gameWorkspace || undefined} style={{ '--app-desktop-dock-width': gameWorkspace ? 'var(--play-workspace-width, 0px)' : showSidebar && !isGamePage && !gameWorkspace ? (dockExpanded ? '220px' : '80px') : '0px' } as React.CSSProperties}>
      {/* 背景层 */}
      <div className="fixed inset-0">
        {backgroundImage ? (
          <div 
            className="absolute inset-0 bg-cover bg-center bg-no-repeat"
            style={{ backgroundImage: `url(${backgroundImage})` }}
          />
        ) : (
          <div className="absolute inset-0 bg-gradient-to-b from-[#0C0F14] via-ink to-[#11151D]" />
        )}
        <div className="absolute inset-0 bg-ink/40" />
      </div>

      {/* DockBar - 桌面端显示 */}
      {showSidebar && !isGamePage && !gameWorkspace && (
        <DockBar className="hidden md:flex" onExpandedChange={setDockExpanded} />
      )}

      {/* 移动端顶部栏 */}
      <div className="fixed top-0 left-0 right-0 z-40 bg-ink/95 backdrop-blur-sm border-b border-line md:hidden">
        <div className="relative flex items-center h-14 px-4">
          {/* 左侧区域 */}
          <div className="flex-1 flex items-center space-x-4">
            {/* 侧边栏切换按钮 */}
            {showSidebar && !isGamePage && !gameWorkspace && (
              <Button
                variant="ghost"
                size="sm"
                aria-label={sidebarOpen ? "关闭导航" : "打开导航"}
                aria-expanded={sidebarOpen}
                onClick={() => setSidebarOpen(!sidebarOpen)}
                className="text-mist hover:bg-raised/60 hover:text-paper"
              >
                {sidebarOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
              </Button>
            )}
            
            {/* Logo */}
            {!isGamePage && (
              <Link href="/" className={gameWorkspace ? 'inline-flex min-h-11 items-center rounded-lg border border-brass/50 bg-raised px-3 py-2 text-sm font-medium text-brass hover:text-paper' : 'font-dossier text-lg font-bold text-brass hover:text-paper transition-colors'}>
                {gameWorkspace ? '← 返回首页' : '人生海海'}
              </Link>
            )}
            
            {/* 游戏页面返回按钮 */}
            {isGamePage && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => router.push('/')}
                className="text-mist hover:bg-raised/60 hover:text-paper"
              >
                ← 返回首页
              </Button>
            )}
          </div>

          {/* 居中页面标题 */}
          {!isGamePage && !gameWorkspace && (() => {
            const title = getPageTitle(router.pathname);
            return title ? (
              <span className="absolute left-1/2 -translate-x-1/2 font-data text-xs tracking-wider text-mist pointer-events-none">
                {title}
              </span>
            ) : null;
          })()}

          {/* 右侧用户菜单 */}
          <div className="flex-1 flex items-center justify-end">
            <UserMenu variant="compact" />
          </div>
        </div>
      </div>

      {/* 游戏中的导航由每轮记录替代。 */}
      {showSidebar && !isGamePage && !gameWorkspace && <>
        {/* 遮罩层 */}
        {sidebarOpen && (
          <div 
            className="fixed inset-x-0 bottom-0 top-14 z-[55] bg-ink/60 md:hidden"
            onClick={() => setSidebarOpen(false)}
          />
        )}
        
        {/* 移动端侧边栏 */}
        <div inert={!sidebarOpen} aria-label="手机导航" className={cn(
          "fixed top-14 left-0 z-[60] h-[calc(100vh-3.5rem)] w-64 bg-panel backdrop-blur-sm border-r border-line transform transition-transform duration-300 md:hidden",
          sidebarOpen ? "translate-x-0" : "-translate-x-full"
        )}>
            <div className="flex h-16 shrink-0 items-center px-6 border-b border-hairline">
              <Link href="/" className="font-dossier text-lg font-bold text-brass hover:text-paper transition-colors">
                人生海海
              </Link>
            </div>
            <div className="flex flex-col h-full">
              <div className="p-4 space-y-2 flex-1">
                {filteredMobileNavItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = router.pathname === item.href || 
                    (item.href !== '/' && router.pathname.startsWith(item.href));
                  
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      onClick={() => setSidebarOpen(false)}
                      className={cn(
                        "flex items-center space-x-3 px-4 py-3 rounded-sm text-sm font-medium transition-all duration-200 w-full",
                        isActive 
                          ? "bg-brass/15 text-brass border border-brass/30" 
                          : "text-mist hover:text-paper hover:bg-raised/60"
                      )}
                    >
                      <Icon className="h-5 w-5" />
                      <span>{item.label}</span>
                    </Link>
                  );
                })}
              </div>
              {/* 移动端用户菜单 */}
              <div className="p-4 border-t border-line">
                <UserMenu />
              </div>
            </div>
        </div>
      </>}

      {/* 主要内容区域 */}
      <div className={cn(
        "app-content relative z-10 transition-[padding-left] duration-300",
        "pt-14 md:pt-0", // 移动端为顶部栏留出空间，桌面端不需要
        // Content and fixed game controls use the same live Dock width.
      )}>




        {/* 页面内容 */}
        <main>
          {children}
        </main>
      </div>
    </div>
  );
};

export default AppLayout;
