export type ModeId = 'archive' | 'nebula' | 'voyage' | 'garden';

export type ModelId = 'armillary' | 'island' | 'book' | 'compass' | 'garden' | 'core';

export type GravityDirection = 'idle' | 'up' | 'down' | 'side';

export type QualityName = 'low' | 'medium' | 'ultra';

export type Project = {
  id: string;
  name: string;
  category: string;
  status: string;
  summary: string;
  liveUrl: string;
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
