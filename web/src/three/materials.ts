import * as THREE from 'three';

/** 피팅룸 빛 색(선형 값). 1보다 큰 값은 블룸(번짐)에 걸린다. 빛이 너무 세지 않게 1.2~1.6 사이로 둔다. */
export const PEARL = new THREE.Color(1.45, 1.36, 1.2);
export const CHAMPAGNE = new THREE.Color(1.3, 1.02, 0.62);
/** 반짝이는 입자 색(아주 조금 따뜻한 흰색) */
export const GLITTER = new THREE.Color(1.0, 0.96, 0.9);

/** 흰 무광 디스플레이 마네킹(유리섬유 마네킹처럼 결이 고르고 반사가 거의 없다) */
export function mannequinMaterial(color = '#dedad5'): THREE.MeshPhysicalMaterial {
  // 환경 반사의 세기는 장면 쪽(scene.environmentIntensity)에서 정한다. 재질의 envMapIntensity는
  // 재질에 envMap을 직접 줄 때만 쓰이고 장면 환경 지도에는 걸리지 않는다(three r163부터).
  return new THREE.MeshPhysicalMaterial({ color, roughness: 0.62, metalness: 0 });
}

export interface DissolveUniforms {
  /** 0이면 하나도 안 보이고 1이면 다 보인다. 그 사이에는 모래알 같은 무늬로 군데군데 생긴다 */
  uSolid: { value: number };
  /** 생기는 경계에 더하는 빛 */
  uEdgeColor: { value: THREE.Color };
  /** 무늬 크기(1 m에 몇 알) */
  uGrain: { value: number };
  /** 높이 방향 쏠림: +1 아래부터, -1 위부터, 0 고르게 */
  uDir: { value: number };
  /** 쏠림을 계산할 높이 범위(모형 좌표 y) */
  uSpan: { value: THREE.Vector2 };
}

export function dissolveUniforms(dir: number, span: [number, number]): DissolveUniforms {
  return {
    uSolid: { value: 0 },
    uEdgeColor: { value: PEARL.clone().multiplyScalar(0.8) },
    uGrain: { value: 58 },
    uDir: { value: dir },
    uSpan: { value: new THREE.Vector2(span[0], span[1]) },
  };
}

/**
 * 입자가 굳어 표면이 되는 재질. 모형 좌표에 붙은 잡음 무늬가 uSolid를 넘는 조각만 그리고,
 * 막 생기는 경계에는 가는 빛을 더한다. 같은 uniforms를 넘기면 여러 재질(티셔츠·목둘레)이 함께 굳는다.
 */
export function withDissolve<T extends THREE.MeshPhysicalMaterial>(material: T, uniforms: DissolveUniforms): T {
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace('#include <common>', '#include <common>\nvarying vec3 vDissolvePos;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvDissolvePos = transformed;');
    shader.fragmentShader = shader.fragmentShader
      .replace(
        '#include <common>',
        /* glsl */ `#include <common>
varying vec3 vDissolvePos;
uniform float uSolid;
uniform vec3 uEdgeColor;
uniform float uGrain;
uniform float uDir;
uniform vec2 uSpan;
float dHash(vec3 p) {
  p = fract(p * 0.3183099 + 0.1);
  p *= 17.0;
  return fract(p.x * p.y * p.z * (p.x + p.y + p.z));
}
float dNoise(vec3 x) {
  vec3 i = floor(x);
  vec3 f = fract(x);
  f = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(mix(dHash(i), dHash(i + vec3(1.0, 0.0, 0.0)), f.x), mix(dHash(i + vec3(0.0, 1.0, 0.0)), dHash(i + vec3(1.0, 1.0, 0.0)), f.x), f.y),
    mix(mix(dHash(i + vec3(0.0, 0.0, 1.0)), dHash(i + vec3(1.0, 0.0, 1.0)), f.x), mix(dHash(i + vec3(0.0, 1.0, 1.0)), dHash(i + vec3(1.0, 1.0, 1.0)), f.x), f.y),
    f.z);
}`,
      )
      .replace(
        '#include <clipping_planes_fragment>',
        /* glsl */ `#include <clipping_planes_fragment>
float dissolveN = dNoise(vDissolvePos * uGrain) * 0.62 + dNoise(vDissolvePos * uGrain * 2.7 + 11.0) * 0.38;
float dissolveH = clamp((vDissolvePos.y - uSpan.x) / max(uSpan.y - uSpan.x, 0.001), 0.0, 1.0);
float dissolveB = uDir > 0.5 ? dissolveH : (uDir < -0.5 ? 1.0 - dissolveH : 0.5);
float dissolveV = dissolveN * 0.62 + dissolveB * 0.38;
float dissolveT = uSolid * 1.3 - 0.15;
if (dissolveV > dissolveT) discard;`,
      )
      .replace(
        '#include <opaque_fragment>',
        'outgoingLight += uEdgeColor * (1.0 - smoothstep(0.0, 0.045, dissolveT - dissolveV));\n#include <opaque_fragment>',
      );
  };
  material.customProgramCacheKey = () => 'dissolve';
  return material;
}

const DOT_FRAGMENT = /* glsl */ `
  varying vec3 vColor;
  varying float vAlpha;
  void main() {
    vec2 d = gl_PointCoord - 0.5;
    float r2 = dot(d, d) * 4.0;
    if (r2 > 1.0) discard;
    float a = vAlpha * (1.0 - r2) * (1.0 - r2);
    if (a < 0.003) discard;
    gl_FragColor = vec4(vColor, a);
  }
`;

/**
 * 반짝이는 점구름(트리포의 생성 효과처럼 작은 흰 입자 수만 개).
 * 점마다 aFrom(uFromMatrix로 옮긴 자리) → position(도착 자리)으로 시차를 두고 흘러가며, 날아가는 동안 몸 둘레를 감아 돈다.
 * 자리 잡은 뒤에도 작게 떨리고 깜빡이며, uFuzz만큼 표면 밖으로 번진다(가장자리가 보풀처럼 흐릿).
 * uWaveY 높이를 지나는 띠는 더 밝다(옷이 내려앉는 느낌).
 * uAppear가 1이면 아직 출발하지 않은 입자는 보이지 않고, 날기 시작하면서 나타난다(사방 먼 곳에서 날아오는 몸 입자).
 */
export function glitterMaterial(pixelRatio: number): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime: { value: 0 },
      uMorph: { value: 1 },
      uFromMatrix: { value: new THREE.Matrix4() },
      uSwirl: { value: 1.4 },
      uFuzz: { value: 0.02 },
      uOpacity: { value: 1 },
      uSize: { value: 2.2 },
      uPixelRatio: { value: pixelRatio },
      uColor: { value: GLITTER.clone() },
      uWaveY: { value: -10 },
      uWave: { value: 0 },
      uAppear: { value: 0 },
    },
    vertexShader: /* glsl */ `
      attribute vec3 aFrom;
      attribute vec3 aNormal;
      attribute vec3 aSeed;
      uniform float uTime;
      uniform float uMorph;
      uniform mat4 uFromMatrix;
      uniform float uSwirl;
      uniform float uFuzz;
      uniform float uOpacity;
      uniform float uSize;
      uniform float uPixelRatio;
      uniform vec3 uColor;
      uniform float uWaveY;
      uniform float uWave;
      uniform float uAppear;
      varying vec3 vColor;
      varying float vAlpha;
      void main() {
        float k = clamp(uMorph * 1.7 - aSeed.x * 0.7, 0.0, 1.0);
        float e = k * k * (3.0 - 2.0 * k);
        vec3 from = (uFromMatrix * vec4(aFrom, 1.0)).xyz;
        vec3 p = mix(from, position, e);
        float fly = sin(3.14159265 * k);
        float ang = fly * uSwirl * (0.5 + aSeed.y);
        float c = cos(ang);
        float s = sin(ang);
        p.xz = vec2(c * p.x - s * p.z, s * p.x + c * p.z);
        p += (aSeed - 0.5) * fly * 0.2;
        float r4 = fract(aSeed.x * 13.7 + aSeed.z * 7.3);
        p += aNormal * pow(r4, 4.0) * uFuzz * e;
        float t = uTime;
        p += vec3(sin(t * 6.1 + aSeed.x * 91.0), sin(t * 5.3 + aSeed.y * 73.0), sin(t * 6.7 + aSeed.z * 57.0)) * 0.0015;
        vec4 world = modelMatrix * vec4(p, 1.0);
        vec4 mv = viewMatrix * world;
        gl_Position = projectionMatrix * mv;
        float tw = 0.5 + 0.5 * sin(t * (2.2 + aSeed.z * 6.0) + aSeed.x * 37.0);
        float bright = (0.28 + 0.72 * aSeed.z) * (0.3 + 0.7 * tw * tw);
        float wave = exp(-pow((world.y - uWaveY) / 0.05, 2.0)) * uWave;
        vColor = uColor;
        vAlpha = uOpacity * bright * (1.0 + wave * 1.6) * (1.0 + fly * 0.6) * mix(1.0, smoothstep(0.0, 0.06, k), uAppear);
        float size = uSize * (0.55 + 0.9 * aSeed.y) * uPixelRatio * (1.0 + wave * 0.4);
        gl_PointSize = max(1.0, size * clamp(2.8 / -mv.z, 0.6, 1.6));
      }
    `,
    fragmentShader: DOT_FRAGMENT,
  });
}

/**
 * 실사 변환의 시작(번쩍인 뒤): 모형 겉의 점(aFrom)이 앞쪽 사진 판(position)으로 납작하게 눌려 붙는다.
 * 날아가는 동안은 크고 밝은 흰 반짝임, 붙으면 찍어 둔 정면 사진(uImage)의 그 자리 색이 된다.
 */
export function peelMaterial(pixelRatio: number, image: THREE.Texture): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: {
      uImage: { value: image },
      uTime: { value: 0 },
      uMorph: { value: 0 },
      uOpacity: { value: 0 },
      uSize: { value: 2.4 },
      uPixelRatio: { value: pixelRatio },
    },
    vertexShader: /* glsl */ `
      attribute vec3 aFrom;
      attribute vec2 aUv;
      attribute vec3 aSeed;
      uniform sampler2D uImage;
      uniform float uTime;
      uniform float uMorph;
      uniform float uOpacity;
      uniform float uSize;
      uniform float uPixelRatio;
      varying vec3 vColor;
      varying float vAlpha;
      void main() {
        float k = clamp(uMorph * 1.6 - aSeed.x * 0.6, 0.0, 1.0);
        float e = k * k * (3.0 - 2.0 * k);
        vec3 p = mix(aFrom, position, e);
        float fly = sin(3.14159265 * k);
        p += (aSeed - 0.5) * fly * 0.1;
        p.z += fly * 0.07 * aSeed.y;
        // 찍어 둔 사진은 배경이 투명하고 가장자리 색이 알파로 곱해져 있다(원래 색으로 되돌린다)
        vec4 tex = texture2D(uImage, aUv);
        vec3 photo = tex.rgb / max(tex.a, 0.05);
        vColor = mix(vec3(1.0, 0.96, 0.9), photo, e * e);
        float tw = 0.6 + 0.4 * sin(uTime * (3.0 + aSeed.z * 5.0) + aSeed.x * 41.0);
        vAlpha = uOpacity * mix(0.85 * tw, 0.95, e);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = max(1.0, uSize * uPixelRatio * mix(0.75 + aSeed.y * 0.75, 1.0, e * e));
      }
    `,
    fragmentShader: DOT_FRAGMENT,
  });
}

/**
 * 실사 변환: 사진 속 사람·옷 자리(알파가 있는 곳)를 이루는 점 격자. uScatter만큼 사방(aNoise: 카드 밖 먼 곳)으로 흩어졌다가,
 * uSettle이 오르면 점마다 다른 때에 날아 돌아와 제자리를 찾는다. 날아다니는 점은 크고 밝은 흰 반짝임, 제자리를 찾은 점은 사진(uImage) 색.
 * 배경 자리 점은 그리지 않는다(뒤의 사진 카드 바탕이 그대로 보인다). uWaveX를 지나는 비스듬한 띠는 밝고 조금 크다(확인 중에는 더 진하게).
 */
export function photoDotsMaterial(pixelRatio: number, image: THREE.Texture): THREE.ShaderMaterial {
  return new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: {
      uImage: { value: image },
      uTime: { value: 0 },
      uScatter: { value: 0 },
      uSettle: { value: 0 },
      uOpacity: { value: 0 },
      uSize: { value: 3 },
      uPixelRatio: { value: pixelRatio },
      uWaveX: { value: -1 },
      uWave: { value: 0 },
    },
    vertexShader: /* glsl */ `
      attribute vec2 aUv;
      attribute vec3 aNoise;
      attribute vec3 aSeed;
      uniform sampler2D uImage;
      uniform float uTime;
      uniform float uScatter;
      uniform float uSettle;
      uniform float uOpacity;
      uniform float uSize;
      uniform float uPixelRatio;
      uniform float uWaveX;
      uniform float uWave;
      varying vec3 vColor;
      varying float vAlpha;
      void main() {
        float s = clamp((uSettle - aSeed.x * 0.85) / 0.15, 0.0, 1.0);
        s = s * s * (3.0 - 2.0 * s);
        float away = uScatter * (1.0 - s);
        float t = uTime;
        vec3 drift = vec3(sin(t * 0.7 + aSeed.y * 20.0), cos(t * 0.6 + aSeed.z * 17.0), sin(t * 0.5 + aSeed.x * 13.0)) * 0.03;
        vec3 p = position + (aNoise + drift) * away;
        vec4 tex = texture2D(uImage, aUv);
        vec3 photo = tex.rgb / max(tex.a, 0.05);
        float subject = smoothstep(0.25, 0.75, tex.a);
        float wave = exp(-pow((aUv.x + (aUv.y - 0.5) * 0.25 - uWaveX) / 0.07, 2.0)) * uWave;
        vColor = mix(vec3(1.0, 0.97, 0.92), photo, 1.0 - away) + wave * 0.35;
        float tw = 0.55 + 0.45 * sin(t * (2.4 + aSeed.z * 5.0) + aSeed.y * 33.0);
        vAlpha = uOpacity * subject * mix(0.9 * tw, 1.0, 1.0 - away) * (1.0 + wave * 0.5);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = max(1.0, uSize * uPixelRatio * mix(1.3 + aSeed.y * 1.4, 1.0, 1.0 - away) * (1.0 + wave * 0.5));
      }
    `,
    fragmentShader: DOT_FRAGMENT,
  });
}
