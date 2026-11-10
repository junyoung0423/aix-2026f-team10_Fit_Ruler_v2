const escapeXml = (text: string) =>
  text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** 가짜 API가 3D 미리보기·프레임 자리에 넣는 그림(티셔츠 실루엣 + 이름표) */
export function placeholderImage(label: string, colorHex: string): string {
  const svg = [
    '<svg xmlns="http://www.w3.org/2000/svg" width="320" height="400" viewBox="0 0 320 400">',
    '<rect width="320" height="400" fill="#eef0f3"/>',
    `<path d="M110 60 L80 70 L40 120 L70 150 L95 130 L95 330 L225 330 L225 130 L250 150 L280 120 L240 70 L210 60 Q160 95 110 60 Z" fill="${escapeXml(colorHex)}" stroke="#7a7f87" stroke-width="2"/>`,
    `<text x="160" y="370" font-family="sans-serif" font-size="16" text-anchor="middle" fill="#4a4f57">${escapeXml(label)}</text>`,
    '</svg>',
  ].join('');
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}
