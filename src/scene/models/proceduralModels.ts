import {
  AdditiveBlending,
  BoxGeometry,
  BufferGeometry,
  CatmullRomCurve3,
  Color,
  ConeGeometry,
  CylinderGeometry,
  DoubleSide,
  Group,
  Line,
  LineBasicMaterial,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  Points,
  PointsMaterial,
  SphereGeometry,
  TorusGeometry,
  Vector3
} from 'three';
import type { ModelId } from '../../types';
import type { ModeSystem } from '../systems/modeSystems';

const standard = (color: number, emissive = color, intensity = 0.25) =>
  new MeshStandardMaterial({
    color,
    emissive,
    emissiveIntensity: intensity,
    metalness: 0.52,
    roughness: 0.32
  });

const line = (color: number) => new LineBasicMaterial({ color, transparent: true, opacity: 0.72 });

export function createModel(id: ModelId, system: ModeSystem): Group {
  if (id === 'island') return createFloatingIsland(system);
  if (id === 'book') return createArchiveBook(system);
  if (id === 'compass') return createCompass(system);
  if (id === 'garden') return createGarden(system);
  if (id === 'core') return createEnergyCore(system);
  return createArmillary(system);
}

export function tintModel(group: Group, system: ModeSystem): void {
  group.traverse((child) => {
    const material = 'material' in child ? child.material : undefined;
    if (material instanceof MeshStandardMaterial || material instanceof MeshBasicMaterial || material instanceof PointsMaterial || material instanceof LineBasicMaterial) {
      material.color = new Color(system.accent);
      if ('emissive' in material && material.emissive instanceof Color) material.emissive = new Color(system.secondary);
    }
  });
}

function createArmillary(system: ModeSystem): Group {
  const group = new Group();
  const ringMaterial = standard(system.accent, system.secondary, 0.35);
  const blueMaterial = standard(system.secondary, system.secondary, 0.45);
  const core = new Mesh(new SphereGeometry(0.42, 48, 32), standard(system.accent, system.accent, 0.85));
  core.position.z = 0.12;
  group.add(core);

  for (let i = 0; i < 4; i += 1) {
    const ring = new Mesh(new TorusGeometry(1.45 - i * 0.18, 0.006, 12, 160), i % 2 ? blueMaterial : ringMaterial);
    ring.rotation.set(Math.PI / 2 + i * 0.35, i * 0.62, i * 0.23);
    group.add(ring);
  }

  const arc = new Line(
    new BufferGeometry().setFromPoints(new CatmullRomCurve3([
      new Vector3(-1.2, -0.35, 0.55),
      new Vector3(-0.2, 0.22, 0.82),
      new Vector3(1.1, 0.36, 0.42)
    ]).getPoints(80)),
    line(system.secondary)
  );
  group.add(arc);
  return group;
}

function createFloatingIsland(system: ModeSystem): Group {
  const group = new Group();
  const base = new Mesh(new ConeGeometry(1.25, 0.9, 7), standard(0x253f4c, system.accent, 0.22));
  base.rotation.x = Math.PI;
  base.position.y = -0.35;
  group.add(base);

  const top = new Mesh(new CylinderGeometry(1.18, 1.28, 0.22, 9), standard(0x375762, system.secondary, 0.25));
  top.position.y = 0.12;
  group.add(top);

  for (let i = 0; i < 18; i += 1) {
    const pebble = new Mesh(new SphereGeometry(0.035 + Math.random() * 0.035, 12, 8), standard(i % 3 ? system.accent : system.secondary, system.accent, 0.35));
    const angle = (i / 18) * Math.PI * 2;
    pebble.position.set(Math.cos(angle) * (0.45 + Math.random() * 0.65), 0.28 + Math.random() * 0.22, Math.sin(angle) * (0.45 + Math.random() * 0.55));
    group.add(pebble);
  }
  return group;
}

function createArchiveBook(system: ModeSystem): Group {
  const group = new Group();
  const pageMaterial = standard(0xf2ddae, system.accent, 0.22);
  const coverMaterial = standard(0x3a2418, system.accent, 0.28);
  const left = new Mesh(new BoxGeometry(0.94, 0.08, 1.28), pageMaterial);
  const right = new Mesh(new BoxGeometry(0.94, 0.08, 1.28), pageMaterial);
  left.position.set(-0.5, 0, 0);
  right.position.set(0.5, 0, 0);
  left.rotation.z = 0.12;
  right.rotation.z = -0.12;
  group.add(left, right);

  const spine = new Mesh(new BoxGeometry(0.10, 0.13, 1.36), coverMaterial);
  spine.position.y = -0.02;
  group.add(spine);

  for (let i = 0; i < 8; i += 1) {
    const mark = new Line(
      new BufferGeometry().setFromPoints([new Vector3(-0.85 + i * 0.12, 0.09, -0.42), new Vector3(-0.55 + i * 0.05, 0.10, 0.45)]),
      line(system.secondary)
    );
    group.add(mark);
  }
  group.rotation.x = -0.38;
  return group;
}

function createCompass(system: ModeSystem): Group {
  const group = new Group();
  const disc = new Mesh(new TorusGeometry(1.2, 0.012, 12, 160), standard(system.accent, system.accent, 0.34));
  group.add(disc);
  for (let i = 0; i < 24; i += 1) {
    const tick = new Mesh(new BoxGeometry(0.012, i % 6 === 0 ? 0.22 : 0.10, 0.012), standard(system.secondary, system.secondary, 0.42));
    tick.position.set(Math.cos((i / 24) * Math.PI * 2) * 1.08, Math.sin((i / 24) * Math.PI * 2) * 1.08, 0);
    tick.rotation.z = (i / 24) * Math.PI * 2;
    group.add(tick);
  }
  const needle = new Mesh(new ConeGeometry(0.12, 1.48, 4), standard(system.secondary, system.secondary, 0.65));
  needle.rotation.z = -Math.PI / 2;
  group.add(needle);
  return group;
}

function createGarden(system: ModeSystem): Group {
  const group = new Group();
  const island = createFloatingIsland(system);
  island.scale.setScalar(0.84);
  group.add(island);

  for (let i = 0; i < 22; i += 1) {
    const stem = new Mesh(new CylinderGeometry(0.008, 0.010, 0.42 + Math.random() * 0.28, 6), standard(0x6f8c54, system.accent, 0.18));
    const angle = (i / 22) * Math.PI * 2;
    const radius = 0.18 + Math.random() * 0.82;
    stem.position.set(Math.cos(angle) * radius, 0.42, Math.sin(angle) * radius);
    stem.rotation.z = (Math.random() - 0.5) * 0.32;
    const bloom = new Mesh(new SphereGeometry(0.045 + Math.random() * 0.04, 12, 8), standard(i % 4 ? system.secondary : 0xf5a3b7, system.secondary, 0.55));
    bloom.position.set(stem.position.x, stem.position.y + 0.28, stem.position.z);
    group.add(stem, bloom);
  }
  return group;
}

function createEnergyCore(system: ModeSystem): Group {
  const group = new Group();
  const core = new Mesh(new SphereGeometry(0.58, 48, 32), new MeshBasicMaterial({ color: system.accent }));
  group.add(core);

  for (let i = 0; i < 3; i += 1) {
    const shell = new Mesh(
      new TorusGeometry(0.88 + i * 0.18, 0.009, 12, 160),
      new MeshBasicMaterial({ color: i % 2 ? system.secondary : system.accent, transparent: true, opacity: 0.78, blending: AdditiveBlending, side: DoubleSide })
    );
    shell.rotation.set(i * 0.6, Math.PI / 2 + i * 0.45, i * 0.25);
    group.add(shell);
  }

  const dustGeometry = new BufferGeometry();
  const points = [];
  for (let i = 0; i < 120; i += 1) {
    const angle = Math.random() * Math.PI * 2;
    const radius = 0.75 + Math.random() * 0.85;
    points.push(new Vector3(Math.cos(angle) * radius, (Math.random() - 0.5) * 1.4, Math.sin(angle) * radius));
  }
  dustGeometry.setFromPoints(points);
  group.add(new Points(dustGeometry, new PointsMaterial({ color: system.secondary, size: 0.025, transparent: true, opacity: 0.82, blending: AdditiveBlending })));
  return group;
}
