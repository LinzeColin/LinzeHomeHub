import type { ModeId } from '../../types';

export type ModeSystem = {
  id: ModeId;
  label: string;
  accent: number;
  secondary: number;
  background: number;
  particleTurbulence: number;
  particleGravity: number;
  particleDriftX: number;
  routeBias: number;
  pollenLift: number;
  spaceImpulse: 'archive-seal' | 'black-hole' | 'warp-jump' | 'bloom-pulse';
  modelTilt: [number, number, number];
  cameraShift: [number, number, number];
};

export const modeSystems: ModeSystem[] = [
  {
    id: 'archive',
    label: 'Archive 档案',
    accent: 0xe9c46a,
    secondary: 0xaebfff,
    background: 0x030713,
    particleTurbulence: 0.018,
    particleGravity: 0.19,
    particleDriftX: -0.006,
    routeBias: -0.13,
    pollenLift: 0,
    spaceImpulse: 'archive-seal',
    modelTilt: [0.96, 0.12, -0.30],
    cameraShift: [0, 0, 0]
  },
  {
    id: 'nebula',
    label: 'Nebula 星云',
    accent: 0x9cd9ff,
    secondary: 0xc9a7ff,
    background: 0x05061a,
    particleTurbulence: 0.045,
    particleGravity: 0.23,
    particleDriftX: 0.002,
    routeBias: 0.03,
    pollenLift: 0,
    spaceImpulse: 'black-hole',
    modelTilt: [0.74, -0.22, -0.10],
    cameraShift: [0.16, 0.03, 0]
  },
  {
    id: 'voyage',
    label: 'Voyage 夜航',
    accent: 0x7bd5ff,
    secondary: 0xe9c46a,
    background: 0x031025,
    particleTurbulence: 0.026,
    particleGravity: 0.21,
    particleDriftX: 0.060,
    routeBias: 0.38,
    pollenLift: 0,
    spaceImpulse: 'warp-jump',
    modelTilt: [0.92, 0.32, -0.38],
    cameraShift: [-0.08, 0.02, 0]
  },
  {
    id: 'garden',
    label: 'Garden 花园',
    accent: 0x9de6b8,
    secondary: 0xe9c46a,
    background: 0x071008,
    particleTurbulence: 0.032,
    particleGravity: 0.16,
    particleDriftX: 0.016,
    routeBias: -0.05,
    pollenLift: 0.034,
    spaceImpulse: 'bloom-pulse',
    modelTilt: [0.82, -0.08, -0.24],
    cameraShift: [0.08, -0.04, 0]
  }
];

export function getModeSystem(id: ModeId): ModeSystem {
  return modeSystems.find((system) => system.id === id) ?? modeSystems[0];
}
