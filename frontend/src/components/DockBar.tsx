import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/router';
import {
  History,
  BookOpen,
  Plus,
} from 'lucide-react';
import UserMenu from '@/components/UserMenu';
import { useAuthStore } from '@/stores/authStore';
import { cn } from '@/lib/utils';

interface DockBarProps {
  className?: string;
  onExpandedChange?: (expanded: boolean) => void;
}

interface NavItem {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  requireAuth?: boolean;
}

const navItems: NavItem[] = [
  { href: '/', label: '首页', icon: BookOpen },
  { href: '/play/package-preview', label: '开始新游戏', icon: Plus, requireAuth: true },
  { href: '/play/records', label: '我的记录', icon: History, requireAuth: true },
];

const DockBar: React.FC<DockBarProps> = ({ className, onExpandedChange }) => {
  const router = useRouter();
  const { isAuthenticated, user } = useAuthStore();
  const [expanded, setExpanded] = useState(false);
  const expand = (value: boolean) => { setExpanded(value); onExpandedChange?.(value); };

  const filteredNavItems = navItems.filter(item =>
    !item.requireAuth || isAuthenticated
  );

  const isActive = (href: string) =>
    (href === '/' && ['/', '/script-center', '/play'].includes(router.pathname)) || router.pathname === href || (href !== '/' && router.pathname.startsWith(href));

  return (
    <div
      className={cn(
        "fixed left-0 top-0 z-50 h-screen flex flex-col overflow-hidden",
        "bg-panel/95 backdrop-blur-sm border-r border-line",
        "transition-all duration-300",
        expanded ? "w-[220px]" : "w-20",
        className
      )}
      aria-label="站点导航"
      onMouseEnter={() => expand(true)}
      onMouseLeave={event => { if (!event.currentTarget.contains(document.activeElement)) expand(false); }}
      onFocusCapture={() => expand(true)}
      onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget) && !event.currentTarget.matches(':hover')) expand(false); }}
    >
      {/* Logo 区域 */}
      <div className="flex h-20 items-center border-b border-line px-4 shrink-0">
        <Link href="/" className="flex items-center gap-3">
          <div className="w-12 h-12 shrink-0 bg-brass/15 border border-brass/40 rounded-sm flex items-center justify-center hover:bg-brass/25 transition-all duration-200">
            <span className="font-dossier text-paper font-bold text-lg">海</span>
          </div>
          <span className={cn(
            "font-dossier text-paper font-bold text-base whitespace-nowrap transition-opacity duration-300",
            expanded ? "opacity-100" : "opacity-0"
          )}>
            人生海海
          </span>
        </Link>
      </div>

      {/* 主要导航区域 */}
      <div className="min-h-0 flex-1 flex flex-col overflow-y-auto overscroll-contain py-6 space-y-2 px-2">
        {filteredNavItems.map((item) => {
          const Icon = item.icon;
          const active = isActive(item.href);

          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "relative flex h-12 shrink-0 items-center rounded-sm transition-all duration-300",
                expanded ? "w-full px-3 gap-3" : "w-12 mx-auto justify-center",
                active
                  ? "bg-brass/15 text-brass border border-brass/30"
                  : "text-mist hover:text-paper hover:bg-raised/60"
              )}
            >
              <Icon className="h-6 w-6 shrink-0" />
              <span className={cn(
                "text-sm font-medium whitespace-nowrap transition-opacity duration-300",
                expanded ? "opacity-100" : "w-0 overflow-hidden opacity-0"
              )}>
                {item.label}
              </span>

              {/* 活跃状态指示器 */}
              {active && (
                <div className="absolute right-0 top-1/2 -translate-y-1/2 w-1 h-6 bg-brass rounded-full" />
              )}
            </Link>
          );
        })}
      </div>

      {/* 底部用户菜单 */}
      <div className="flex shrink-0 flex-col pb-6 border-t border-line pt-4 px-2">
        <div className={cn(
          "flex items-center rounded-sm transition-all duration-300",
          expanded ? "w-full gap-3 px-3" : "w-12 mx-auto justify-center"
        )}>
          <div className="shrink-0">
            <UserMenu variant="compact" />
          </div>
          {user && (
            <span className={cn(
              "text-sm font-medium text-paper whitespace-nowrap transition-opacity duration-300 truncate",
              expanded ? "opacity-100" : "w-0 overflow-hidden opacity-0"
            )}>
              {user.nickname || user.username}
            </span>
          )}
        </div>
      </div>
    </div>
  );
};

export default DockBar;
