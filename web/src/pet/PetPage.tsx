/**
 * W10-A · 桌面宠物浮窗入口页（路由 `/pet`）。
 *
 * 刻意不套主 Layout、不要求登录：浮窗是桌面宠物，游客也能看到自己的宠物。
 * 透明背景交给 Tauri 窗口的 ``transparent: true``；本页根节点不设任何底色。
 */
import { PetWidget } from './PetWidget';

export default function PetPage() {
  return (
    <div style={{ minHeight: '100vh', background: 'transparent' }}>
      <PetWidget />
    </div>
  );
}
