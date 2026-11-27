/**
 * 테스트(mannequin.test.ts)가 마네킹 GLB 파일을 읽을 때만 쓰는 Node 파일 읽기 타입.
 * 앱은 Node 타입을 쓰지 않으므로(tsconfig types: vite/client) 필요한 함수 하나만 적어 둔다.
 */
declare module 'node:fs' {
  export function readFileSync(path: URL): Uint8Array;
}
