import * as THREE from 'three';

/** 캔버스로 그린 텍스처(외부 그림 파일 없이). */

function canvas(w: number, h: number): [HTMLCanvasElement, CanvasRenderingContext2D] {
  const c = document.createElement('canvas');
  c.width = w;
  c.height = h;
  const ctx = c.getContext('2d');
  if (!ctx) throw new Error('2D 캔버스를 쓸 수 없습니다.');
  return [c, ctx];
}

/** 턴테이블 윗면: 가장자리에 줄자 눈금(1°마다 짧게, 10°마다 길게) */
export function rulerDisc(): THREE.Texture {
  const S = 1024;
  const [c, ctx] = canvas(S, S);
  const cx = S / 2;
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, S, S);
  ctx.strokeStyle = 'rgba(255,255,255,1)';
  for (let deg = 0; deg < 360; deg++) {
    const a = (deg * Math.PI) / 180;
    const long = deg % 10 === 0;
    const r1 = S * 0.47;
    const r0 = r1 - (long ? S * 0.03 : S * 0.014);
    ctx.lineWidth = long ? 2.2 : 1.1;
    ctx.globalAlpha = long ? 0.9 : 0.5;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(a) * r0, cx + Math.sin(a) * r0);
    ctx.lineTo(cx + Math.cos(a) * r1, cx + Math.sin(a) * r1);
    ctx.stroke();
  }
  ctx.globalAlpha = 0.35;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(cx, cx, S * 0.43, 0, Math.PI * 2);
  ctx.stroke();
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.NoColorSpace;
  t.anisotropy = 4;
  return t;
}

/** 둥근 모서리 사각형(사진 카드 알파). outline이면 테두리만 */
export function roundedRect(w: number, h: number, radius: number, outline = 0): THREE.Texture {
  const [c, ctx] = canvas(w, h);
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, w, h);
  const path = (inset: number, r: number) => {
    ctx.beginPath();
    ctx.roundRect(inset, inset, w - inset * 2, h - inset * 2, r);
  };
  ctx.fillStyle = '#fff';
  path(outline > 0 ? 1 : 0, radius);
  ctx.fill();
  if (outline > 0) {
    ctx.globalCompositeOperation = 'destination-out';
    path(outline, Math.max(0, radius - outline));
    ctx.fill();
  }
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.NoColorSpace;
  return t;
}
