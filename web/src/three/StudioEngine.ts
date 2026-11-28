import * as THREE from 'three';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import type { StepKey } from '../progress/steps';
import { bodyParams, defaultGarment, teeLayout, type BodyInput, type BodyParams, type GarmentShape, type TeeLayout } from './bodyModel';
import { approach, clamp01, disposeObject, easeInOut, easeOut, samplePoints, toGeometry, toScreen } from './common';
import {
  CHAMPAGNE,
  dissolveUniforms,
  glitterMaterial,
  mannequinMaterial,
  peelMaterial,
  photoDotsMaterial,
  withDissolve,
  type DissolveUniforms,
} from './materials';
import { buildMeshes, type BuiltMeshes } from './meshClient';
import { outerStarts, photoFrame, photoGrid, scatterOffsets, seeded, seeds } from './particleShapes';
import { roundedRect } from './textures';

/**
 * 결과 화면 피팅룸의 3D 장면(three.js). 트리포 AI가 모델을 만들 때처럼 작고 흰 입자 수만 개가 반짝이며 모양을 이룬다.
 * 배경은 투명해서 피팅룸 카드의 색이 그대로 비친다(효과 화면과 카드가 한 덩어리로 보인다).
 * - 체형 생성: 입자가 화면 바깥 사방(위·아래·옆·앞뒤)에서 날아와 입력 치수에 맞춘 마네킹 모양 점구름이 된다(대기 중에는 빈 무대).
 *   기다리는 동안 점구름은 천천히 돌며 점점 또렷해진다(치수 이름표는 붙이지 않는다).
 * - 가상 착용: 몸 점구름이 모래알처럼 굳어 흰 마네킹이 되고, 입자가 화면 바깥 사방에서 날아와 고른 옷 치수의 티셔츠 모양으로 마네킹 위에 모인다.
 *   티셔츠는 끝까지 입자로 남는다(옷 색 천으로 굳히지 않는다). 기다리는 동안 밝은 띠가 어깨에서 밑단으로 지나간다.
 * - 3D 촬영: 마네킹과 입자 티셔츠가 정면으로 돌아서고, 입자가 사방에서 날아와 둘레에 사진 틀(뷰파인더: 네 귀퉁이 꺾쇠 + 얇은 테두리)을 만든다.
 *   기다리는 동안 빛 띠가 틀 위에서 아래로 되풀이해 훑고, 띠가 지나는 자리의 티셔츠·틀 입자가 반짝인다.
 * - 실사 변환: 한 번 번쩍인 뒤 모형이 틀 안으로 납작하게 눌려 정면 사진 한 장이 되고, 사진 속 사람·옷이 입자로 부서져 사방으로 흩어진다.
 *   기다리는 동안 입자가 사방에서 날아 돌아와 사진을 다시 채운다(처음 몇 초에 일부가 바로, 나머지는 단계 내내 조금씩). 검증 중에는 빛 띠가 지나간다.
 * 실제 진행이 효과보다 빠르면 효과를 조금 빨리 돌려 따라가고, 단계마다 효과를 끝까지 보여 준다.
 * 움직임 줄이기(reducedMotion)면 애니메이션 없이 단계마다 마지막 모습 한 장만 그린다.
 */

export interface StudioState {
  step: StepKey | null;
  /** 단계가 아직 시작 전(대기열). 체형 생성 전이면 입자를 날려 보내지 않고 기다린다 */
  preparing: boolean;
  checking: boolean;
  failed: boolean;
}

export interface StudioEngineOptions {
  body: Partial<BodyInput>;
  garment: GarmentShape | null;
  reducedMotion: boolean;
}

type CameraKind = 'full' | 'upper' | 'card';
type Glitter = THREE.Points<THREE.BufferGeometry, THREE.ShaderMaterial>;

const ORDER: StepKey[] = ['body', 'drape', 'render', 'realize'];

/** 효과 시간(초) */
const T = {
  /** 몸 입자가 사방에서 날아와 모이는 시간 */
  morph: 2.8,
  /** 입자 → 매끈한 표면 */
  solid: 1.4,
  teeGather: 2.8,
  turn: 1.3,
  /** 사진 틀 입자가 사방에서 날아와 모이는 시간 */
  frameIn: 1.8,
  /** 빛 띠가 틀 위에서 아래까지 훑는 시간과 되풀이 주기 */
  scanSweep: 2.4,
  scanPeriod: 3.2,
  /** 모형이 사진 판으로 눌려 붙는 시간 */
  peel: 2.2,
  cardIn: 0.9,
  /** 사진 점이 사방으로 흩어지는 시간 */
  scatter: 1.4,
};
/** 3D 촬영: 돌아선 뒤 사진을 찍어 두는 때, 사진 틀이 날아오기 시작하는 때, 빛 띠가 훑기 시작하는 때 */
const R_CAPTURE = T.turn + 0.15;
const R_FRAME = 0.5;
const R_SCAN = R_FRAME + T.frameIn;
/** 실사 변환: 모형이 눌려 붙기 시작하는 때, 사진 카드가 나타나는 때, 사진 점이 카드를 이어받는 때 */
const X_PEEL = 0.15;
const X_CARD = X_PEEL + T.peel * 0.75;
const X_DOTS = X_CARD + T.cardIn + 0.25;

/** 다음 단계로 넘어가기 전에 이 단계 효과를 보여 주는 최소 시간 */
const MIN_SHOW: Record<StepKey, number> = {
  body: T.morph + 0.7,
  drape: T.solid * 0.55 + T.teeGather + 0.6,
  render: R_SCAN + T.scanSweep,
  realize: Infinity,
};
/** 효과가 실제 진행보다 늦으면 이만큼 빨리 돌려 따라잡는다 */
const CATCH_UP = 2.2;
/** 기다리는 동안 입자가 또렷해지는 빠르기(초). 단계마다 보통 걸리는 시간에 맞춘다 */
const SETTLE_TAU = { body: 7, drape: 50, realize: 45 };

const PHOTO_COLS = 165;
const PHOTO_ROWS = 220;

interface SampleSet {
  positions: Float32Array;
  normals: Float32Array;
}

/** 한 순간의 장면 모습(단계와 그 단계 안의 시간으로 정해진다) */
interface Look {
  bodyMorph: number;
  bodyDots: number;
  bodyFuzz: number;
  bodySolid: number;
  teeMorph: number;
  teeDots: number;
  teeFuzz: number;
  teeWave: number;
  peelMorph: number;
  peelDots: number;
  card: number;
  /** 사진 카드 바탕(회색). 실사 변환 단계에서 사람·옷이 입자로 흩어져도 남는다 */
  back: number;
  border: number;
  dots: number;
  scatter: number;
  settle: number;
  dotsWave: number;
  ring: number;
  shadow: number;
  spin: number;
  /** 3D 촬영의 사진 틀 입자: 날아온 정도와 밝기 */
  frameMorph: number;
  frameDots: number;
  /** 빛 띠의 밝기(0~1)와 위치(0 틀 위 → 1 틀 아래) */
  scan: number;
  scanPos: number;
  /** 실사 변환 시작의 번쩍임 */
  flash: number;
  camera: CameraKind;
}

function emptyLook(): Look {
  return {
    bodyMorph: 1,
    bodyDots: 0,
    bodyFuzz: 0,
    bodySolid: 0,
    teeMorph: 1,
    teeDots: 0,
    teeFuzz: 0,
    teeWave: 0,
    peelMorph: 0,
    peelDots: 0,
    card: 0,
    back: 0,
    border: 0,
    dots: 0,
    scatter: 0,
    settle: 0,
    dotsWave: 0,
    ring: 0,
    shadow: 0,
    spin: 0,
    frameMorph: 1,
    frameDots: 0,
    scan: 0,
    scanPos: 0,
    flash: 0,
    camera: 'full',
  };
}

export class StudioEngine {
  private readonly opts: StudioEngineOptions;
  private readonly host: HTMLElement;
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(30, 0.75, 0.05, 40);
  private readonly captureCam = new THREE.PerspectiveCamera(30, 0.75, 0.05, 40);
  private readonly composer: EffectComposer;
  private readonly bloom: UnrealBloomPass;
  private readonly clock = new THREE.Clock();
  private readonly params: BodyParams;
  private readonly layout: TeeLayout;
  /** 턴테이블 위(돈다) */
  private readonly model = new THREE.Group();
  /** 돌지 않는 것(바닥 그림자·사진 카드) */
  private readonly world = new THREE.Group();
  private readonly ground: THREE.Mesh<THREE.CircleGeometry, THREE.ShadowMaterial>;
  private readonly ring: Glitter;

  private readonly bodySolid: DissolveUniforms;
  private body: THREE.Mesh | null = null;
  private bodyDots: Glitter | null = null;
  private teeDots: Glitter | null = null;
  private peel: Glitter | null = null;
  private samples: { body: SampleSet; tee: SampleSet | null } | null = null;

  private readonly card = new THREE.Group();
  private readonly cardMedia: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
  private readonly cardBorder: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
  private readonly cardBack: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;
  private readonly photoDots: Glitter;
  private readonly cardCenter: THREE.Vector3;
  private readonly cardSize: { w: number; h: number };
  private readonly captureTarget: THREE.WebGLRenderTarget;
  private captured = false;
  /** 3D 촬영의 사진 틀·빛 띠와 실사 변환 시작의 번쩍임(카드 자리에 서고, 카드처럼 흔들리지 않는다) */
  private readonly frameGroup = new THREE.Group();
  private readonly frame: Glitter;
  private readonly scanLine: Glitter;
  private readonly flash: THREE.Mesh<THREE.PlaneGeometry, THREE.MeshBasicMaterial>;


  private state: StudioState = { step: null, preparing: true, checking: false, failed: false };
  private initialized = false;
  private ready = false;
  private vis: StepKey = 'body';
  private visTime = 0;
  private time = 0;
  private checkBoost = 0;
  private turn: { from: number; to: number } | null = null;
  private readonly camPos = new THREE.Vector3();
  private readonly camTarget = new THREE.Vector3();
  private size = { w: 1, h: 1 };
  private raf = 0;
  private running = false;
  private visible = true;
  private disposed = false;
  private readonly resizeObserver: ResizeObserver;
  private readonly intersection: IntersectionObserver;

  constructor(host: HTMLElement, opts: StudioEngineOptions) {
    this.host = host;
    this.opts = opts;
    this.params = bodyParams(opts.body);
    this.layout = teeLayout(this.params, opts.garment ?? defaultGarment(this.params));
    const p = this.params;
    const L = this.layout;
    this.bodySolid = dissolveUniforms(1, [0, p.height]);

    // 배경을 투명하게(alpha) 그려 카드 배경이 비치게 한다
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    renderer.setClearColor(0x000000, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0.9;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.domElement.className = 'studio-canvas';
    host.appendChild(renderer.domElement);
    this.renderer = renderer;
    const pr = renderer.getPixelRatio();

    const pmrem = new THREE.PMREMGenerator(renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    // 환경 지도는 그림자 쪽을 살짝 여는 정도로만(세면 흰 마네킹이 하얗게 떠서 근육 음영이 사라진다)
    this.scene.environmentIntensity = 0.28;
    pmrem.dispose();

    // 빛: 위 앞쪽의 따뜻한 주광 + 뒤쪽 양옆의 윤곽광(실루엣) + 약한 보조광. 굳은 마네킹에만 닿는다(입자는 스스로 빛난다).
    this.scene.add(new THREE.HemisphereLight(0xfff1de, 0x15110e, 0.15));
    const key = new THREE.SpotLight(0xffeedd, 1.7, 0, 0.42, 0.8, 0);
    key.position.set(2.2, 3.4, 2.2);
    key.target.position.set(0, 1.0, 0);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.bias = -0.0004;
    key.shadow.normalBias = 0.02;
    key.shadow.radius = 5;
    this.scene.add(key, key.target);
    const rimL = new THREE.DirectionalLight(0xdfe5ff, 0.6);
    rimL.position.set(-2.6, 2.4, -2.4);
    const rimR = new THREE.DirectionalLight(0xffdcb8, 0.45);
    rimR.position.set(2.8, 2.0, -2.2);
    const fill = new THREE.DirectionalLight(0xfff3e6, 0.3);
    fill.position.set(-1.6, 1.4, 2.6);
    this.scene.add(rimL, rimR, fill);
    this.scene.add(this.model, this.world);

    // 바닥: 그림자만 받는 투명한 면 + 줄자 눈금 모양의 입자 고리(턴테이블과 함께 돈다)
    // 깊이를 쓰지 않아 바닥 아래에서 날아오는 입자도 가려지지 않는다
    this.ground = new THREE.Mesh(new THREE.CircleGeometry(7, 72), new THREE.ShadowMaterial({ opacity: 0, depthWrite: false }));
    this.ground.rotation.x = -Math.PI / 2;
    this.ground.position.y = -0.002;
    this.ground.receiveShadow = true;
    this.world.add(this.ground);
    this.ring = this.floorRing();
    this.model.add(this.ring);

    // 정면 사진을 찍는 카메라와 사진 카드. 카드는 모형이 있던 자리(정면 카메라에서 본 사진 틀 그대로)에 선다.
    const top = p.y.chin + 0.03;
    const bottom = L.hemY - 0.07;
    const tanV = Math.tan((15 * Math.PI) / 180);
    const cy = (top + bottom) / 2;
    const d = Math.max((top - bottom) / 2 / tanV, (Math.abs(L.cuffR[0]) + 0.08) / (tanV * 0.75));
    this.captureCam.position.set(0, cy, d);
    this.captureCam.lookAt(0, cy, 0);
    this.captureCam.updateMatrixWorld();
    const zc = 0.02;
    const ch = 2 * (d - zc) * tanV;
    const cw = ch * 0.75;
    this.cardSize = { w: cw, h: ch };
    this.cardCenter = new THREE.Vector3(0, cy, zc);
    this.captureTarget = new THREE.WebGLRenderTarget(600, 800, { type: THREE.HalfFloatType, samples: 4 });
    this.cardMedia = new THREE.Mesh(
      new THREE.PlaneGeometry(cw, ch),
      new THREE.MeshBasicMaterial({ map: this.captureTarget.texture, alphaMap: roundedRect(300, 400, 12), transparent: true, opacity: 0, depthWrite: false }),
    );
    this.cardMedia.renderOrder = 2;
    this.cardBorder = new THREE.Mesh(
      new THREE.PlaneGeometry(cw * 1.014, ch * 1.0105),
      new THREE.MeshBasicMaterial({
        color: CHAMPAGNE.clone().multiplyScalar(0.7),
        alphaMap: roundedRect(300, 400, 13, 2),
        transparent: true,
        opacity: 0,
        depthWrite: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    this.cardBorder.position.z = -0.002;
    this.cardBorder.renderOrder = 0;
    // 사진은 배경을 비워 두고 찍으므로(사람·옷만) 바탕을 따로 둔다. 흰 마네킹·입자가 또렷하게 어두운 회색
    this.cardBack = new THREE.Mesh(
      new THREE.PlaneGeometry(cw, ch),
      new THREE.MeshBasicMaterial({ color: 0x2e3033, alphaMap: roundedRect(300, 400, 12), transparent: true, opacity: 0, depthWrite: false }),
    );
    this.cardBack.position.z = -0.001;
    this.cardBack.renderOrder = 1;
    const grid = photoGrid(PHOTO_COLS, PHOTO_ROWS, cw, ch);
    const dg = new THREE.BufferGeometry();
    dg.setAttribute('position', new THREE.BufferAttribute(grid.positions, 3));
    dg.setAttribute('aUv', new THREE.BufferAttribute(grid.uvs, 2));
    // 흩어질 자리: 카드 밖 사방 먼 곳(가운데에서 그 점 쪽으로). 앞뒤 이동은 줄여 카메라 앞을 가리지 않게
    dg.setAttribute('aNoise', new THREE.BufferAttribute(scatterOffsets(grid.positions, ch * 1.4, ch * 2.6, 0.55, 0.3, seeded(41)), 3));
    dg.setAttribute('aSeed', new THREE.BufferAttribute(seeds(PHOTO_COLS * PHOTO_ROWS, seeded(43)), 3));
    dg.translate(0, 0, 0.003);
    this.photoDots = new THREE.Points(dg, photoDotsMaterial(pr, this.captureTarget.texture));
    this.photoDots.frustumCulled = false;
    this.photoDots.renderOrder = 3;
    this.card.add(this.cardBorder, this.cardBack, this.cardMedia, this.photoDots);
    this.card.position.copy(this.cardCenter);
    this.world.add(this.card);

    // 3D 촬영의 사진 틀(뷰파인더): 입자가 사방 먼 곳에서 날아와 카드 가장자리에 모인다
    const frameRand = seeded(61);
    const fr = photoFrame(cw * 1.02, ch * 1.015, 2600, frameRand);
    const faceCamera = (n: number) => {
      const normals = new Float32Array(n * 3);
      for (let i = 2; i < normals.length; i += 3) normals[i] = 1;
      return normals;
    };
    this.frame = this.glitter(fr.positions, outerStarts(fr.positions, [0, 0, 0], 1.2, 2.2, 0.55, frameRand), faceCamera(2600), fr.seeds);
    this.frame.material.uniforms.uAppear.value = 1;
    this.frame.material.uniforms.uSwirl.value = 0.6;
    // 빛 띠: 틀 너비의 가는 입자 줄. 모형 위로 겹쳐 보이게 깊이 검사를 끈다
    const nScan = 900;
    const scanPos = new Float32Array(nScan * 3);
    for (let i = 0; i < nScan; i++) {
      scanPos[i * 3] = (frameRand() - 0.5) * cw * 0.98;
      scanPos[i * 3 + 1] = (frameRand() - 0.5) * 0.006;
      scanPos[i * 3 + 2] = frameRand() * 0.01;
    }
    this.scanLine = this.glitter(scanPos, scanPos.slice(), faceCamera(nScan), seeds(nScan, frameRand));
    this.scanLine.material.depthTest = false;
    this.scanLine.material.uniforms.uSwirl.value = 0;
    this.scanLine.material.uniforms.uSize.value = 2.6;
    this.scanLine.renderOrder = 5;
    // 실사 변환 시작의 번쩍임: 카드 크기의 흰 면(더해지는 빛)
    this.flash = new THREE.Mesh(
      new THREE.PlaneGeometry(cw * 1.06, ch * 1.04),
      new THREE.MeshBasicMaterial({
        color: 0xffffff,
        alphaMap: roundedRect(300, 400, 12),
        transparent: true,
        opacity: 0,
        depthWrite: false,
        depthTest: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    this.flash.position.z = 0.06;
    this.flash.renderOrder = 6;
    this.flash.visible = false;
    this.frameGroup.add(this.frame, this.scanLine, this.flash);
    this.frameGroup.position.copy(this.cardCenter);
    this.world.add(this.frameGroup);

    this.composer = new EffectComposer(renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    // 입자가 겹쳐 1보다 밝아진 곳만 은은하게 번진다(흰 마네킹은 1을 넘지 않게 빛을 맞춰 번지지 않는다)
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.38, 0.45, 1.05);
    // 번진 빛은 색에만 더하고 투명도는 그대로 둔다. 원래 방식은 번짐을 섞으며 화면 전체를 불투명하게 칠해
    // 투명한 배경이 검게 덮이고 카드 색이 가려진다
    const blend = this.bloom.blendMaterial;
    blend.blending = THREE.CustomBlending;
    blend.blendEquation = THREE.AddEquation;
    blend.blendSrc = THREE.SrcAlphaFactor;
    blend.blendDst = THREE.OneFactor;
    blend.blendSrcAlpha = THREE.ZeroFactor;
    blend.blendDstAlpha = THREE.OneFactor;
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.resize();
    const start = this.preset('full');
    this.camPos.copy(start.pos);
    this.camTarget.copy(start.target);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(host);
    this.intersection = new IntersectionObserver(([entry]) => {
      this.visible = entry?.isIntersecting ?? true;
      this.updateRunning();
    });
    this.intersection.observe(host);
    document.addEventListener('visibilitychange', this.onVisibility);

    buildMeshes(opts.body, opts.garment, true)
      .then((meshes) => this.onMeshes(meshes))
      .catch(() => undefined);
    this.updateRunning();
    this.renderStill();
  }

  // ---------- 바깥에서 부르는 것

  setState(next: StudioState) {
    this.state = { ...next };
    if (!this.initialized) {
      // 처음 붙을 때는 지금 단계부터 시작한다(새로고침하면 앞 단계 효과를 다시 돌리지 않는다)
      this.initialized = true;
      this.enter(next.step ?? 'body');
    }
    this.updateRunning();
    this.renderStill();
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    this.running = false;
    this.resizeObserver.disconnect();
    this.intersection.disconnect();
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.cardMedia.material.map = null;
    disposeObject(this.scene);
    this.captureTarget.dispose();
    this.scene.environment?.dispose();
    this.bloom.dispose();
    this.composer.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
  }

  // ---------- 만들기

  private glitter(to: Float32Array, from: Float32Array, normals: Float32Array, seed: Float32Array): Glitter {
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(to, 3));
    g.setAttribute('aFrom', new THREE.BufferAttribute(from, 3));
    g.setAttribute('aNormal', new THREE.BufferAttribute(normals, 3));
    g.setAttribute('aSeed', new THREE.BufferAttribute(seed, 3));
    const points = new THREE.Points(g, glitterMaterial(this.renderer.getPixelRatio()));
    points.frustumCulled = false;
    return points;
  }

  /** 턴테이블 자리: 입자로 된 고리와 줄자 눈금(10°마다, 30°마다 길게) */
  private floorRing(): Glitter {
    const rand = seeded(29);
    const n = 3400;
    const R = 0.5;
    const pos = new Float32Array(n * 3);
    const normals = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      let a: number;
      let r: number;
      if (i < n * 0.6) {
        a = rand() * Math.PI * 2;
        r = R + (rand() - 0.5) * 0.006;
      } else {
        const deg = Math.floor(rand() * 36) * 10;
        a = (deg * Math.PI) / 180 + (rand() - 0.5) * 0.005;
        r = R - 0.008 - rand() * (deg % 30 === 0 ? 0.05 : 0.025);
      }
      pos[i * 3] = Math.cos(a) * r;
      pos[i * 3 + 1] = 0.001;
      pos[i * 3 + 2] = Math.sin(a) * r;
      normals[i * 3 + 1] = 1;
    }
    const ring = this.glitter(pos, pos.slice(), normals, seeds(n, rand));
    ring.material.uniforms.uSwirl.value = 0;
    ring.material.uniforms.uFuzz.value = 0;
    ring.material.uniforms.uSize.value = 1.9;
    return ring;
  }

  private onMeshes(meshes: BuiltMeshes) {
    if (this.disposed) return;
    const p = this.params;
    const L = this.layout;
    const rand = seeded(17);

    // 몸: 매끈한 표면(입자가 굳으면 보인다) + 사방 먼 곳(화면 바깥)에서 날아와 마네킹 겉에 앉는 입자
    this.body = new THREE.Mesh(toGeometry(meshes.body), withDissolve(mannequinMaterial(), this.bodySolid));
    this.body.receiveShadow = true;
    this.model.add(this.body);
    const nBody = 46000;
    const bodyPts = samplePoints(meshes.body, nBody, 3);
    const s = p.height / 1.75;
    const far = outerStarts(bodyPts.positions, [0, p.height * 0.5, 0], 2.0 * s, 3.2 * s, 0.55, rand);
    this.bodyDots = this.glitter(bodyPts.positions, far, bodyPts.normals, seeds(nBody, rand));
    this.bodyDots.material.uniforms.uAppear.value = 1;
    this.bodyDots.material.uniforms.uSwirl.value = 1.0;
    this.model.add(this.bodyDots);

    // 티셔츠: 모양(메시)은 입자를 뽑는 데만 쓴다. 사방 먼 곳에서 날아와 티셔츠 겉에 앉는다(흰 입자, 옷 색은 칠하지 않는다)
    let teeSamples: SampleSet | null = null;
    if (meshes.tee) {
      const nTee = 32000;
      const teePts = samplePoints(meshes.tee, nTee, 5);
      const teeFar = outerStarts(teePts.positions, [0, (L.topY + L.hemY) / 2, 0], 1.8 * s, 2.8 * s, 0.55, rand);
      this.teeDots = this.glitter(teePts.positions, teeFar, teePts.normals, seeds(nTee, rand));
      this.teeDots.material.uniforms.uAppear.value = 1;
      this.teeDots.material.uniforms.uSwirl.value = 1.0;
      this.model.add(this.teeDots);
      teeSamples = teePts;
    }

    this.samples = { body: bodyPts, tee: teeSamples };
    this.ready = true;
    this.renderStill();
  }

  // ---------- 단계

  private enter(step: StepKey) {
    this.vis = step;
    this.visTime = 0;
    this.checkBoost = 0;
    if (step === 'render') {
      const full = Math.PI * 2;
      const from = this.model.rotation.y;
      this.turn = { from, to: Math.ceil(from / full - 1e-6) * full };
    } else {
      this.turn = null;
    }
  }

  // ---------- 매 프레임

  private readonly onVisibility = () => this.updateRunning();

  private updateRunning() {
    const should = !this.disposed && this.visible && !document.hidden && !this.opts.reducedMotion && !this.state.failed;
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
    this.update(dt);
    this.composer.render();
  };

  /** 애니메이션을 돌리지 않을 때(움직임 줄이기·멈춤·처음) 지금 모습 한 장. 시간은 흐르지 않고 위치만 맞춘다 */
  private renderStill() {
    if (this.running || this.disposed) return;
    this.update(10, false);
    this.composer.render();
  }

  private resize() {
    const w = Math.max(1, this.host.clientWidth);
    const h = Math.max(1, this.host.clientHeight);
    this.size = { w, h };
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.bloom.resolution.set(w / 2, h / 2);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    if (!this.running) this.renderStill();
  }

  private preset(kind: CameraKind): { pos: THREE.Vector3; target: THREE.Vector3 } {
    const p = this.params;
    const L = this.layout;
    const tanV = Math.tan(((this.camera.fov * Math.PI) / 180) / 2);
    const tanH = tanV * this.camera.aspect;
    const fit = (h: number, w: number) => Math.max(h / 2 / tanV, w / 2 / tanH);
    if (kind === 'full') {
      const d = fit(p.height * 1.14, (Math.abs(p.joints.wristR[0]) + 0.34) * 2);
      const ty = p.height * 0.5;
      return { pos: new THREE.Vector3(0, ty + d * 0.06, d), target: new THREE.Vector3(0, ty, 0) };
    }
    if (kind === 'upper') {
      const top = p.height + 0.06;
      const bottom = L.hemY - 0.16;
      const d = fit(top - bottom, (Math.abs(L.cuffR[0]) + 0.22) * 2);
      const ty = (top + bottom) / 2;
      return { pos: new THREE.Vector3(0, ty + d * 0.07, d), target: new THREE.Vector3(0, ty, 0) };
    }
    // 사진 틀(카드)이 화면에 여유 있게 들어오는 정면
    const c = this.cardCenter;
    const d = fit(this.cardSize.h * 1.2, this.cardSize.w * 1.2);
    return { pos: new THREE.Vector3(0, c.y, c.z + d), target: c.clone() };
  }

  /** 단계와 그 안의 시간 → 장면 모습 */
  private look(step: StepKey, t: number): Look {
    const reduced = this.opts.reducedMotion;
    const s = emptyLook();
    if (step === 'body') {
      const formed = t - T.morph;
      s.bodyMorph = clamp01(t / T.morph);
      s.bodyDots = 1;
      s.bodyFuzz = formed > 0 ? 0.03 - 0.022 * (1 - Math.exp(-formed / SETTLE_TAU.body)) : 0.03;
      s.ring = easeInOut(clamp01((t - T.morph * 0.5) / 1.2));
      s.spin = 0.22;
      return s;
    }
    if (step === 'drape') {
      const solid = easeInOut(clamp01(t / T.solid));
      s.bodySolid = solid;
      s.bodyDots = 1 - easeInOut(clamp01(t / (T.solid * 0.85)));
      s.bodyFuzz = 0.008 * (1 - solid);
      const t0 = T.solid * 0.55;
      const formed = t - t0 - T.teeGather;
      s.teeMorph = clamp01((t - t0) / T.teeGather);
      s.teeDots = 1;
      s.teeFuzz = formed > 0 ? 0.026 - 0.02 * (1 - Math.exp(-formed / SETTLE_TAU.drape)) : 0.026;
      s.teeWave = formed > 0 && !reduced ? Math.min(1, formed / 1.5) : 0;
      s.ring = 1;
      s.shadow = 0.42 * solid;
      s.spin = 0.16;
      s.camera = 'upper';
      return s;
    }
    if (step === 'render') {
      // 정면으로 돌아선 모형(흰 마네킹 + 입자 티셔츠) 둘레에 사진 틀 입자가 사방에서 날아와 모이고,
      // 기다리는 동안 빛 띠가 틀 위에서 아래로 되풀이해 훑는다
      s.bodySolid = 1;
      s.teeDots = 1;
      s.teeFuzz = 0.006;
      s.ring = 1;
      s.shadow = 0.42;
      s.frameMorph = clamp01((t - R_FRAME) / T.frameIn);
      s.frameDots = 1;
      const since = t - R_SCAN;
      if (since > 0 && !reduced) {
        const phase = since % T.scanPeriod;
        if (phase < T.scanSweep) {
          const pos = phase / T.scanSweep;
          s.scanPos = pos;
          s.scan = easeInOut(clamp01(pos / 0.08)) * (1 - easeInOut(clamp01((pos - 0.92) / 0.08)));
        }
      }
      s.teeWave = s.scan;
      s.camera = 'card';
      return s;
    }
    // 실사 변환: 번쩍인 뒤 모형이 틀 안으로 눌려 사진 한 장이 되고(틀 입자는 카드 테두리가 나타나며 사라진다),
    // 사진 점이 카드를 이어받아 사방으로 흩어졌다가 기다리는 동안 날아 돌아온다
    const flash = reduced ? 0 : t < 0.06 ? t / 0.06 : Math.exp(-(t - 0.06) / 0.14);
    const out = reduced ? 0 : 1 - easeInOut(clamp01((t - X_PEEL - 0.05) / (T.peel * 0.7)));
    const cardIn = reduced ? 1 : easeOut(clamp01((t - X_CARD) / T.cardIn));
    s.flash = 0.6 * flash;
    s.bodySolid = out;
    s.teeDots = out;
    s.teeFuzz = 0.006;
    s.shadow = 0.42 * out;
    s.peelMorph = clamp01((t - X_PEEL) / T.peel);
    s.peelDots = t >= X_PEEL && !reduced ? easeOut(clamp01((t - X_PEEL) / 0.25)) * (1 - cardIn) : 0;
    s.frameDots = 1 - cardIn;
    s.card = reduced ? 1 : cardIn * (1 - easeInOut(clamp01((t - X_DOTS - 0.1) / 0.6)));
    s.back = cardIn;
    s.border = cardIn * 0.7;
    s.dots = reduced ? 0 : easeOut(clamp01((t - X_DOTS) / 0.4));
    s.scatter = easeInOut(clamp01((t - X_DOTS - 0.25) / T.scatter));
    // 돌아오기: 처음 4초에 4분의 1이 바로 돌아오고, 나머지는 단계 내내 천천히(검증 중이면 모두)
    const back = t - (X_DOTS + 0.25 + T.scatter * 0.7);
    const settle = back > 0 ? 0.25 * easeOut(clamp01(back / 4)) + 0.75 * (1 - Math.exp(-back / SETTLE_TAU.realize)) : 0;
    s.settle = Math.max(settle, this.checkBoost);
    s.dotsWave = this.state.checking ? 1 : 0.35;
    s.camera = 'card';
    return s;
  }

  private update(dt: number, advance = true) {
    const st = this.state;
    const reduced = this.opts.reducedMotion;

    // 1) 보여 줄 단계: 효과가 끝나기 전에는 다음 단계로 넘어가지 않고, 늦으면 빨리 돌려 따라간다
    const want: StepKey = st.step ?? 'body';
    const wi = ORDER.indexOf(want);
    const vi = ORDER.indexOf(this.vis);
    if (wi < vi || (wi > vi && reduced)) this.enter(want);
    else if (wi > vi && this.visTime >= MIN_SHOW[this.vis]) this.enter(ORDER[vi + 1]);
    const lagging = wi > ORDER.indexOf(this.vis);
    if (advance && !st.failed) {
      this.time += dt;
      if (this.ready) this.visTime += dt * (lagging ? CATCH_UP : 1);
      if (this.vis === 'realize' && st.checking) this.checkBoost = approach(this.checkBoost, 1, 0.9, dt);
    }
    if (reduced) this.visTime = 60;
    // 체형 생성 대기 중에는 입자를 날려 보내지 않는다(빈 무대)
    else if (this.vis === 'body' && (st.preparing || st.step === null)) this.visTime = 0;
    const t = this.visTime;

    // 2) 정면 사진은 돌아선 뒤 한 번 찍는다(실사 변환부터 시작하면 바로 찍는다)
    if (this.ready && !this.captured && ((this.vis === 'render' && t >= R_CAPTURE) || this.vis === 'realize')) this.capture();

    const look = this.look(this.vis, t);

    // 턴테이블: 3D 촬영 단계에서는 정해진 시간 안에 정확히 정면으로
    if (this.turn) this.model.rotation.y = this.turn.from + (this.turn.to - this.turn.from) * easeInOut(clamp01(t / T.turn));
    else if (advance && !st.failed && !reduced) this.model.rotation.y += look.spin * dt;
    this.model.updateMatrixWorld();

    // 카메라
    const goal = this.preset(look.camera);
    this.camPos.set(approach(this.camPos.x, goal.pos.x, 1.7, dt), approach(this.camPos.y, goal.pos.y, 1.7, dt), approach(this.camPos.z, goal.pos.z, 1.7, dt));
    this.camTarget.set(
      approach(this.camTarget.x, goal.target.x, 1.9, dt),
      approach(this.camTarget.y, goal.target.y, 1.9, dt),
      approach(this.camTarget.z, goal.target.z, 1.9, dt),
    );
    this.camera.position.copy(this.camPos);
    this.camera.lookAt(this.camTarget);
    this.camera.updateMatrixWorld();

    this.setGlitter(this.bodyDots, look.bodyMorph, look.bodyDots, look.bodyFuzz, 0);

    // 사진 틀·빛 띠(3D 촬영)와 번쩍임(실사 변환 시작). 빛 띠 높이는 틀 위 → 아래
    const bandLocal = this.cardSize.h / 2 - look.scanPos * this.cardSize.h;
    const bandWorld = this.cardCenter.y + bandLocal;
    this.setGlitter(this.frame, look.frameMorph, look.frameDots * (1 + 1.5 * look.flash), 0, look.scan);
    this.frame.material.uniforms.uWaveY.value = bandWorld;
    this.setGlitter(this.scanLine, 1, look.scan, 0, 0);
    this.scanLine.position.y = bandLocal;
    this.flash.material.opacity = look.flash;
    this.flash.visible = look.flash > 0.003;

    const L = this.layout;
    this.setGlitter(this.teeDots, look.teeMorph, look.teeDots, look.teeFuzz, look.teeWave);
    if (this.teeDots) {
      const period = 4.2;
      const k = (this.time % period) / period;
      // 가상 착용: 어깨 → 밑단으로 지나가는 띠. 3D 촬영: 사진 틀을 훑는 빛 띠와 같은 높이
      this.teeDots.material.uniforms.uWaveY.value = this.vis === 'render' ? bandWorld : L.topY + 0.05 - k * (L.topY - L.hemY + 0.14);
    }
    this.setGlitter(this.ring, 1, look.ring * 0.55, 0, 0);

    // 굳은 마네킹
    this.bodySolid.uSolid.value = look.bodySolid;
    if (this.body) {
      this.body.visible = look.bodySolid > 0.001;
      this.body.castShadow = look.bodySolid > 0.4;
    }
    this.ground.material.opacity = look.shadow;

    // 정면 사진 판과 실사 변환의 사진 점
    if (this.peel) {
      const u = this.peel.material.uniforms;
      u.uTime.value = this.time;
      u.uMorph.value = look.peelMorph;
      u.uOpacity.value = look.peelDots;
      this.peel.visible = look.peelDots > 0.003;
    }
    this.cardMedia.material.opacity = look.card;
    this.cardBorder.material.opacity = look.border;
    this.cardBack.material.opacity = look.back;
    const du = this.photoDots.material.uniforms;
    du.uTime.value = this.time;
    du.uOpacity.value = look.dots;
    du.uScatter.value = look.scatter;
    du.uSettle.value = look.settle;
    du.uWave.value = reduced ? 0 : look.dotsWave;
    du.uWaveX.value = ((this.time % 3.4) / 3.4) * 1.7 - 0.35;
    this.photoDots.visible = look.dots > 0.003;
    this.card.visible = look.back + look.border + look.dots + look.peelDots > 0.003;
    if (!reduced) {
      this.card.position.y = this.cardCenter.y + Math.sin(this.time * 0.8) * 0.004;
      this.card.rotation.y = Math.sin(this.time * 0.5) * 0.03;
    }
    if (this.card.visible) this.sizeDots();
  }

  private setGlitter(points: Glitter | null, morph: number, opacity: number, fuzz: number, wave: number) {
    if (!points) return;
    const u = points.material.uniforms;
    u.uTime.value = this.time;
    u.uMorph.value = morph;
    u.uOpacity.value = opacity;
    u.uFuzz.value = fuzz;
    u.uWave.value = wave;
    points.visible = opacity > 0.003;
  }

  /** 사진 점 크기: 화면에 보이는 카드 높이를 격자 줄 수로 나눈 간격보다 조금 크게(빈틈 없이) */
  private sizeDots() {
    const { w, h } = this.size;
    const half = this.cardSize.h / 2;
    const a = toScreen(this.card.localToWorld(new THREE.Vector3(0, half, 0)), this.camera, w, h);
    const b = toScreen(this.card.localToWorld(new THREE.Vector3(0, -half, 0)), this.camera, w, h);
    if (!a || !b) return;
    const spacing = Math.abs(a.y - b.y) / PHOTO_ROWS;
    this.photoDots.material.uniforms.uSize.value = spacing * 1.35;
    if (this.peel) this.peel.material.uniforms.uSize.value = spacing * 2.0;
  }

  // ---------- 정면 사진 찍기

  /** 정면에서 본 마네킹과 입자 티셔츠를 배경 없이(투명) 찍고, 모형 겉의 점이 사진 판의 어디로 갈지 정한다 */
  private capture() {
    if (!this.body) return;
    this.captured = true;
    const rotation = this.model.rotation.y;
    const solid = this.bodySolid.uSolid.value;
    this.world.visible = false;
    this.ring.visible = false;
    if (this.bodyDots) this.bodyDots.visible = false;
    this.body.visible = true;
    this.bodySolid.uSolid.value = 1;
    // 티셔츠는 자리 잡은 입자 그대로(옷 색 없이) 사진에 담는다
    this.setGlitter(this.teeDots, 1, 1, 0.004, 0);
    this.model.rotation.y = 0;
    this.model.updateMatrixWorld();

    this.renderer.setRenderTarget(this.captureTarget);
    this.renderer.render(this.scene, this.captureCam);
    this.renderer.setRenderTarget(null);

    this.world.visible = true;
    this.model.rotation.y = rotation;
    this.model.updateMatrixWorld();
    this.bodySolid.uSolid.value = solid;
    this.buildPeel();
  }

  /** 모형 겉의 점(정면을 본 자세) → 사진 판 위 자리와 사진 속 색 위치 */
  private buildPeel() {
    if (!this.samples || this.peel) return;
    const { w, h } = this.cardSize;
    const c = this.cardCenter;
    const v = new THREE.Vector3();
    const from: number[] = [];
    const to: number[] = [];
    const uv: number[] = [];
    const take = (set: SampleSet, every: number) => {
      const n = set.positions.length / 3;
      for (let i = 0; i < n; i += every) {
        const x = set.positions[i * 3];
        const y = set.positions[i * 3 + 1];
        const z = set.positions[i * 3 + 2];
        v.set(x, y, z).project(this.captureCam);
        if (Math.abs(v.x) > 1 || Math.abs(v.y) > 1) continue;
        from.push(x - c.x, y - c.y, z - c.z);
        to.push((v.x * w) / 2, (v.y * h) / 2, 0.004);
        uv.push(v.x * 0.5 + 0.5, v.y * 0.5 + 0.5);
      }
    };
    if (this.samples.tee) take(this.samples.tee, 1);
    take(this.samples.body, this.samples.tee ? 2 : 1);
    const count = to.length / 3;
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(to), 3));
    g.setAttribute('aFrom', new THREE.BufferAttribute(new Float32Array(from), 3));
    g.setAttribute('aUv', new THREE.BufferAttribute(new Float32Array(uv), 2));
    g.setAttribute('aSeed', new THREE.BufferAttribute(seeds(count, seeded(53)), 3));
    this.peel = new THREE.Points(g, peelMaterial(this.renderer.getPixelRatio(), this.captureTarget.texture));
    this.peel.frustumCulled = false;
    this.peel.renderOrder = 3;
    this.peel.visible = false;
    this.card.add(this.peel);
  }
}
