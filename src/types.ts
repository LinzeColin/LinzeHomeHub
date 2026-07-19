export type ModeId = 'archive' | 'nebula' | 'voyage' | 'garden';

export type ModelId = 'armillary' | 'island' | 'book' | 'compass' | 'garden' | 'core';

export type GravityDirection = 'idle' | 'up' | 'down' | 'side';

export type QualityName = 'low' | 'medium' | 'ultra';

export type CompatibilityLevel = 'L2';

export type DeploymentStatus = 'Live' | 'Deploy-ready' | 'Protected';

export type Project = {
  id: string;
  name: string;
  category: string;
  compatibilityLevel: CompatibilityLevel;
  deploymentStatus: DeploymentStatus;
  futureLevel: 'L3 gated';
  summary: string;
  liveUrl: string;
  repo?: string;   // 所属顶级 GitHub 仓;未标注=公开入口
  fallbackUrl: string;
  mode: ModeId;
};

export type PointerState = {
  x: number;
  y: number;
  active: boolean;
};

export type ScrollGravityState = {
  direction: GravityDirection;
  velocityY: number;
  velocityX: number;
  gravityX: number;
  gravityY: number;
  energy: number;
  spaceActive: boolean;
};
