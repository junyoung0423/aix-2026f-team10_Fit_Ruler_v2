import * as THREE from 'three';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import type { MeasurementField } from '../api/contract';
import { bodyParams, type BodyInput, type BodyParams } from './bodyModel';
import { approach, disposeObject, placeLabel, toGeometry, toScreen } from './common';
import { mannequinMaterial } from './materials';
import { buildMeshes } from './meshClient';
import { rulerDisc } from './textures';

/**
 * 입력 화면의 3D 전신 마네킹(밝은 화면). 입력한 치수로 몸을 다시 만들고, 칸을 고르면 그 치수를 재는 곳
 * (키 자·어깨너비 선·가슴둘레/허리둘레 고리·팔길이 선)이 진하게 표시된다. 끌어서 돌려 볼 수 있다.
 */

export interface FigureOptions {
  labels: HTMLElement;
  reducedMotion: boolean;
  onReady?: () => void;
}

const ACCENT = new THREE.Color('#b59a6e');
const ACCENT_INK = new THREE.Color('#7a5f3a');

interface Marker {
  group: THREE.Group;
  materials: THREE.MeshBasicMaterial[];
  label: HTMLDivElement;
  text: HTMLSpanElement;
  anchor: () => THREE.Vector3;
  /** 몸과 같이 도는 표시인지(키 자는 돌지 않는다) */
  rotates: boolean;
}

const FIELDS: MeasurementField[] = ['height_cm', 'shoulder_cm', 'chest_cm', 'waist_cm', 'arm_length_cm'];

/** 키 자를 손목에서 얼마나 바깥에 둘지(m). 자 눈금이 팔길이 선(손목에서 4.5 cm 바깥)에 닿지 않게 */
const RULER_GAP = 0.09;
/** 키 이름표와 자 사이 간격(m) */
const LABEL_GAP = 0.02;
/**
 * 구도를 잡을 때 쓰는 이름표 폭(px, 보통 값이 들어간 이름표의 폭). 실제 글자 폭으로 잡으면 값을 지우거나 고칠 때마다
 * 마네킹 크기가 흔들린다. 고른 칸의 이름표는 굵어져 몇 px 넓어지는데, 그만큼은 캔버스 안쪽으로 밀려 들어온다.
 */
const FRAME_LABEL_W = { height: 96, shoulder: 122, chest: 129, waist: 122 };
/** 오른쪽 이름표의 연결선(16 px)과 간격(6 px)은 몸 가장자리에 걸쳐도 된다(글자 칸은 몸 밖에 남는다) */
const LINE_OVERLAP = 22;
const FRAME_PAD = 4;
const NAME: Record<MeasurementField, string> = {
  height_cm: '키',
  shoulder_cm: '어깨너비',
  chest_cm: '가슴둘레',
  waist_cm: '허리둘레',
  arm_length_cm: '팔길이',
};

export class FigureEngine {
  private readonly host: HTMLElement;
  private readonly opts: FigureOptions;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(28, 0.75, 0.05, 30);
  private readonly model = new THREE.Group();
  private readonly world = new THREE.Group();
  private readonly clock = new THREE.Clock();
  private readonly bodyMaterial = mannequinMaterial();
  private body: THREE.Mesh | null = null;
  private params: BodyParams;
  private markers: Partial<Record<MeasurementField, Marker>> = {};
  private values: Partial<Record<MeasurementField, string | null>> = {};
  private active: MeasurementField | null = null;
  private buildToken = 0;
  private time = 0;
  private dragging = false;
  private lastX = 0;
  private idleAfter = 0;
  private frontUntil = 0;
  private raf = 0;
  private running = false;
  private visible = true;
  private disposed = false;
  private size = { w: 1, h: 1 };
  private readonly resizeObserver: ResizeObserver;
  private readonly intersection: IntersectionObserver;

  constructor(host: HTMLElement, opts: FigureOptions) {
    this.host = host;
    this.opts = opts;
    this.params = bodyParams({});

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.85;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.className = 'figure-canvas';
    host.appendChild(renderer.domElement);
    this.renderer = renderer;

    const pmrem = new THREE.PMREMGenerator(renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    // 환경 지도는 그림자 쪽을 살짝 밝히는 정도로만(세면 흰 마네킹이 하얗게 떠서 근육 음영이 사라진다)
    this.scene.environmentIntensity = 0.3;
    pmrem.dispose();
    // 오른쪽 위 옆에서 오는 주광으로 가슴·복근·허벅지 굴곡에 음영이 지게 하고, 왼쪽 앞 보조광으로 그림자를 조금 연다
    this.scene.add(new THREE.HemisphereLight(0xfffaf2, 0x6f6455, 0.18));
    const fill = new THREE.DirectionalLight(0xffffff, 0.45);
    fill.position.set(-1.6, 1.2, 2.2);
    this.scene.add(fill);
    const key = new THREE.DirectionalLight(0xfff4e8, 2.4);
    key.position.set(2.2, 3.0, 1.6);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.camera.left = -1;
    key.shadow.camera.right = 1;
    key.shadow.camera.top = 2.2;
    key.shadow.camera.bottom = -0.4;
    key.shadow.bias = -0.0005;
    key.shadow.radius = 6;
    const rim = new THREE.DirectionalLight(0xffffff, 0.7);
    rim.position.set(-2.2, 2.2, -2.2);
    this.scene.add(key, rim);

    // 바닥: 그림자만 받는 면 + 줄자 눈금이 있는 원판
    const shadow = new THREE.Mesh(new THREE.PlaneGeometry(4, 4), new THREE.ShadowMaterial({ opacity: 0.14 }));
    shadow.rotation.x = -Math.PI / 2;
    shadow.receiveShadow = true;
    const ticks = new THREE.Mesh(
      new THREE.CircleGeometry(0.52, 96),
      new THREE.MeshBasicMaterial({ color: ACCENT, alphaMap: rulerDisc(), transparent: true, opacity: 0.85, depthWrite: false }),
    );
    ticks.rotation.x = -Math.PI / 2;
    ticks.position.y = 0.001;
    this.world.add(shadow, ticks);
    this.scene.add(this.model, this.world);

    this.resize();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(host);
    this.intersection = new IntersectionObserver(([entry]) => {
      this.visible = entry?.isIntersecting ?? true;
      this.updateRunning();
    });
    this.intersection.observe(host);
    document.addEventListener('visibilitychange', this.onVisibility);
    const canvas = renderer.domElement;
    canvas.addEventListener('pointerdown', this.onDown);
    window.addEventListener('pointermove', this.onMove);
    window.addEventListener('pointerup', this.onUp);
    this.updateRunning();
  }

  /** 치수가 바뀌면 몸과 표시를 다시 만든다(워커에서). values는 이름표에 보일 값(빈칸이면 null) */
  setBody(body: Partial<BodyInput>, values: Partial<Record<MeasurementField, string | null>>) {
    this.values = values;
    this.updateLabelTexts();
    const token = ++this.buildToken;
    buildMeshes(body, null, false)
      .then((meshes) => {
        if (this.disposed || token !== this.buildToken) return;
        this.params = bodyParams(body);
        const geometry = toGeometry(meshes.body);
        if (this.body) {
          this.body.geometry.dispose();
          this.body.geometry = geometry;
        } else {
          this.body = new THREE.Mesh(geometry, this.bodyMaterial);
          this.body.castShadow = true;
          this.body.receiveShadow = true;
          this.model.add(this.body);
          this.opts.onReady?.();
        }
        this.buildMarkers();
        this.renderOnce();
      })
      .catch(() => undefined);
  }

  setActive(field: MeasurementField | null) {
    this.active = field;
    if (field) this.frontUntil = this.time + 3;
    this.updateMarkerStyle();
    this.renderOnce();
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.resizeObserver.disconnect();
    this.intersection.disconnect();
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.renderer.domElement.removeEventListener('pointerdown', this.onDown);
    window.removeEventListener('pointermove', this.onMove);
    window.removeEventListener('pointerup', this.onUp);
    for (const m of Object.values(this.markers)) m?.label.remove();
    disposeObject(this.scene);
    this.scene.environment?.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }

  // ---------- 표시

  private tube(points: THREE.Vector3[], closed: boolean, radius = 0.0032): THREE.Mesh<THREE.TubeGeometry, THREE.MeshBasicMaterial> {
    const curve = new THREE.CatmullRomCurve3(points, closed, 'catmullrom', closed ? 0.5 : 0);
    const geometry = new THREE.TubeGeometry(curve, closed ? 160 : 48, radius, 6, closed);
    return new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({ color: ACCENT.clone(), transparent: true, opacity: 0.85, depthTest: true }));
  }

  private dot(at: THREE.Vector3): THREE.Mesh<THREE.SphereGeometry, THREE.MeshBasicMaterial> {
    const m = new THREE.Mesh(new THREE.SphereGeometry(0.009, 16, 12), new THREE.MeshBasicMaterial({ color: ACCENT.clone(), transparent: true, opacity: 0.9 }));
    m.position.copy(at);
    return m;
  }

  private makeLabel(side: 'left' | 'right'): { label: HTMLDivElement; text: HTMLSpanElement } {
    const label = document.createElement('div');
    label.className = `callout callout-light is-on is-${side}`;
    const line = document.createElement('span');
    line.className = 'callout-line';
    const text = document.createElement('span');
    text.className = 'callout-text';
    label.append(line, text);
    this.opts.labels.appendChild(label);
    return { label, text };
  }

  private buildMarkers() {
    for (const m of Object.values(this.markers)) {
      if (!m) continue;
      disposeObject(m.group);
      m.group.removeFromParent();
      m.label.remove();
    }
    this.markers = {};
    const p = this.params;
    const ring = (y: number, a: number, b: number, cz: number) => {
      const pts: THREE.Vector3[] = [];
      for (let i = 0; i < 48; i++) {
        const t = (i / 48) * Math.PI * 2;
        pts.push(new THREE.Vector3(Math.cos(t) * (a + 0.014), y, cz + Math.sin(t) * (b + 0.014)));
      }
      return pts;
    };
    const rulerX = this.rulerX();
    const make = (field: MeasurementField, objects: THREE.Mesh[], side: 'left' | 'right', anchor: () => THREE.Vector3, rotates: boolean) => {
      const group = new THREE.Group();
      group.add(...objects);
      (rotates ? this.model : this.world).add(group);
      const { label, text } = this.makeLabel(side);
      this.markers[field] = { group, materials: objects.map((o) => o.material as THREE.MeshBasicMaterial), label, text, anchor, rotates };
    };

    // 키: 바닥부터 머리 끝까지의 자(10 cm마다 눈금)
    const rulerParts: THREE.Mesh[] = [this.tube([new THREE.Vector3(rulerX, 0, 0), new THREE.Vector3(rulerX, p.height, 0)], false, 0.0026)];
    for (let y = 0; y <= p.height + 1e-6; y += 0.1) {
      rulerParts.push(this.tube([new THREE.Vector3(rulerX, y, 0), new THREE.Vector3(rulerX + 0.03, y, 0)], false, 0.0018));
    }
    rulerParts.push(this.tube([new THREE.Vector3(rulerX - 0.02, p.height, 0), new THREE.Vector3(0, p.height, 0)], false, 0.0012));
    // 키 이름표는 자 왼쪽, 머리 끝 높이(자 꼭대기에서 머리 끝으로 긋는 선과 같은 높이)
    make('height_cm', rulerParts, 'left', () => new THREE.Vector3(rulerX - LABEL_GAP, p.height, 0), false);

    const sy = p.y.shoulder + 0.022;
    const sl = new THREE.Vector3(-(p.shoulderHalf + 0.004), sy, 0.01);
    const sr = new THREE.Vector3(p.shoulderHalf + 0.004, sy, 0.01);
    make('shoulder_cm', [this.tube([sl, sr], false), this.dot(sl), this.dot(sr)], 'right', () => new THREE.Vector3(p.shoulderHalf + 0.03, sy + 0.04, 0), true);

    make('chest_cm', [this.tube(ring(p.y.chest, p.chest.a, p.chest.b, 0.014), true)], 'right', () => new THREE.Vector3(p.chest.a + 0.03, p.y.chest, 0), true);
    make('waist_cm', [this.tube(ring(p.y.waist, p.waist.a, p.waist.b, 0.002), true)], 'right', () => new THREE.Vector3(p.waist.a + 0.03, p.y.waist, 0), true);

    // 팔길이: 사람의 오른쪽 어깨끝 → 팔꿈치 → 손목(정면에서 보면 왼쪽 팔)
    const j = p.joints;
    const off = new THREE.Vector3(-0.045, 0, 0.02);
    const a0 = new THREE.Vector3(...j.shoulderR).add(new THREE.Vector3(-0.03, 0.04, 0));
    const a1 = new THREE.Vector3(...j.elbowR).add(off);
    const a2 = new THREE.Vector3(...j.wristR).add(off);
    make('arm_length_cm', [this.tube([a0, a1, a2], false), this.dot(a0), this.dot(a2)], 'left', () => a1.clone().add(new THREE.Vector3(-0.03, 0, 0)), true);

    this.updateLabelTexts();
    this.updateMarkerStyle();
  }

  private updateLabelTexts() {
    for (const field of FIELDS) {
      const m = this.markers[field];
      if (!m) continue;
      const v = this.values[field];
      m.text.textContent = v ? `${NAME[field]} ${v} cm` : `${NAME[field]} —`;
    }
  }

  private updateMarkerStyle() {
    for (const field of FIELDS) {
      const m = this.markers[field];
      if (!m) continue;
      const on = this.active === field;
      const dim = this.active !== null && !on;
      for (const mat of m.materials) {
        mat.color.copy(on ? ACCENT_INK : ACCENT);
        mat.opacity = dim ? 0.28 : on ? 1 : 0.8;
      }
      m.group.scale.setScalar(1);
      m.label.classList.toggle('is-active', on);
      m.label.classList.toggle('is-dim', dim);
    }
  }

  // ---------- 매 프레임

  private readonly onVisibility = () => this.updateRunning();

  private readonly onDown = (event: PointerEvent) => {
    this.dragging = true;
    this.lastX = event.clientX;
    this.idleAfter = this.time + 3.5;
  };

  private readonly onMove = (event: PointerEvent) => {
    if (!this.dragging) return;
    this.model.rotation.y += (event.clientX - this.lastX) * 0.012;
    this.lastX = event.clientX;
    this.idleAfter = this.time + 3.5;
    this.frontUntil = 0;
    if (!this.running) this.renderOnce();
  };

  private readonly onUp = () => {
    this.dragging = false;
  };

  private updateRunning() {
    const should = !this.disposed && this.visible && !document.hidden && !this.opts.reducedMotion;
    if (should && !this.running) {
      this.running = true;
      this.clock.getDelta();
      this.raf = requestAnimationFrame(this.loop);
    } else if (!should && this.running) {
      this.running = false;
      cancelAnimationFrame(this.raf);
    }
  }

  private readonly loop = () => {
    if (!this.running) return;
    this.raf = requestAnimationFrame(this.loop);
    const dt = Math.min(0.05, this.clock.getDelta());
    this.time += dt;
    if (!this.dragging) {
      if (this.time < this.frontUntil) this.model.rotation.y = approach(this.model.rotation.y, 0, 3, dt);
      else if (this.time > this.idleAfter) {
        // 가만히 있을 때는 좌우로 천천히 살짝 돈다(정면 기준 ±12°)
        const goal = Math.sin(this.time * 0.35) * 0.21;
        this.model.rotation.y = approach(this.model.rotation.y, goal, 1.2, dt);
      }
    }
    this.render();
  };

  private renderOnce() {
    if (!this.running && !this.disposed) this.render();
  }

  private resize() {
    const w = Math.max(1, this.host.clientWidth);
    const h = Math.max(1, this.host.clientHeight);
    this.size = { w, h };
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.renderOnce();
  }

  /** 키 자의 가로 위치(m): 사람의 오른팔(화면 왼쪽) 손목에서 RULER_GAP만큼 바깥 */
  private rulerX(): number {
    return -(Math.abs(this.params.joints.wristR[0]) + RULER_GAP);
  }

  /**
   * 화면 구도: 몸이 있는 깊이에서 1 m가 몇 px인지(scale)와 화면 가운데에 올 가로 위치(m).
   * 키의 1.1배가 세로로 들어가고, 자 왼쪽의 키 이름표와 몸 오른쪽 이름표가 잘리지 않는 가장 큰 배율을 고른 뒤
   * 양쪽 여백이 같아지게 가운데를 옮긴다.
   */
  private frame(w: number, h: number): { scale: number; centerX: number } {
    const p = this.params;
    const lx = this.rulerX() - LABEL_GAP; // 키 이름표의 오른쪽 끝
    const hand = Math.abs(p.joints.wristR[0]) + 0.06; // 화면 오른쪽 손의 바깥
    const right = [
      { x: p.shoulderHalf + 0.03, w: FRAME_LABEL_W.shoulder - LINE_OVERLAP },
      { x: p.chest.a + 0.03, w: FRAME_LABEL_W.chest - LINE_OVERLAP },
      { x: p.waist.a + 0.03, w: FRAME_LABEL_W.waist - LINE_OVERLAP },
      { x: hand, w: 0 },
    ];
    const room = w - 2 * FRAME_PAD - FRAME_LABEL_W.height;
    let scale = h / (p.height * 1.1);
    for (const r of right) scale = Math.min(scale, (room - r.w) / (r.x - lx));
    scale = Math.max(scale, 1);
    // 화면에서 x = 0이 올 자리(px): 키 이름표가 들어가는 가장 왼쪽 자리와 오른쪽 이름표·손이 들어가는 가장 오른쪽 자리의 가운데
    const lo = FRAME_PAD + FRAME_LABEL_W.height - lx * scale;
    const hi = Math.min(...right.map((r) => w - FRAME_PAD - r.w - r.x * scale));
    return { scale, centerX: (w / 2 - (lo + hi) / 2) / scale };
  }

  private render() {
    const p = this.params;
    const { w, h } = this.size;
    const tanV = Math.tan(((this.camera.fov * Math.PI) / 180) / 2);
    const { scale, centerX } = this.frame(w, h);
    // 몸이 있는 깊이(z = 0)에서 1 m가 scale px로 보이는 거리
    const d = h / (2 * tanV * scale);
    const ty = p.height * 0.5;
    this.camera.position.set(centerX, ty + d * 0.05, d);
    this.camera.lookAt(centerX, ty, 0);
    this.camera.updateMatrixWorld();
    this.model.updateMatrixWorld();
    for (const field of FIELDS) {
      const m = this.markers[field];
      if (!m) continue;
      const local = m.anchor();
      const world = m.rotates ? this.model.localToWorld(local.clone()) : local;
      const s = toScreen(world, this.camera, w, h);
      if (s) placeLabel(m.label, s.x, s.y, w);
      m.label.classList.toggle('is-hidden', m.rotates && world.z < -0.1);
    }
    this.renderer.render(this.scene, this.camera);
  }
}
