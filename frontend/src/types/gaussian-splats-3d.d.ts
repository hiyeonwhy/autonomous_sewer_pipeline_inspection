// @mkkellogg/gaussian-splats-3d 는 타입 정의를 제공하지 않아 사용하는 표면만 선언한다.
declare module '@mkkellogg/gaussian-splats-3d' {
  export interface SplatViewerOptions {
    rootElement?: HTMLElement;
    /** SharedArrayBuffer 사용 여부. 켜려면 COOP/COEP 헤더가 필요하다. */
    sharedMemoryForWorkers?: boolean;
    /** 렌더링에 사용할 구면조화 차수(0~2). 파일에 담긴 차수 이하만 유효. */
    sphericalHarmonicsDegree?: number;
    cameraUp?: [number, number, number];
    initialCameraPosition?: [number, number, number];
    initialCameraLookAt?: [number, number, number];
    useBuiltInControls?: boolean;
    selfDrivenMode?: boolean;
  }

  export interface AddSplatSceneOptions {
    showLoadingUI?: boolean;
    progressiveLoad?: boolean;
    splatAlphaRemovalThreshold?: number;
    onProgress?: (percentComplete: number, label?: string, status?: number) => void;
  }

  export class Viewer {
    constructor(options?: SplatViewerOptions);
    addSplatScene(path: string, options?: AddSplatSceneOptions): Promise<void>;
    start(): void;
    dispose(): Promise<void>;
  }
}
