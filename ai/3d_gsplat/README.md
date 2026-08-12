# 3D Gaussian Splatting — 탐사 현장 3D 재구성

로봇이 촬영한 다시점 이미지로부터 **3D Gaussian Splatting(3DGS)** 기법을 이용해 탐사 현장을 3D로 재구성하는 모듈입니다. [nerfstudio-project/gsplat](https://github.com/nerfstudio-project/gsplat) 라이브러리(commit `bf302ef`, 2026-07 기준 최신)를 기반으로 합니다.

> 3DGS 최신 기법·오픈소스 생태계 리서치 정리는 [RESEARCH.md](./RESEARCH.md) 참고.

## 사용 기술

- **모델/기법**: 3D Gaussian Splatting — 장면을 수십만 개의 이방성 3D 가우시안(위치·공분산·불투명도·SH 색상)으로 표현하고, 미분 가능한 CUDA 래스터라이저로 입력 이미지에 맞춰 최적화 (L1 + SSIM loss)
- **초기화**: COLMAP sparse point cloud
- **밀도 제어 전략**: `default`(adaptive density control) / `mcmc` 선택 가능
- **프레임워크**: gsplat (PyTorch + CUDA), torch 2.9 / numpy 2 / CUDA GPU 필수

## 입력 / 출력

- **입력**: COLMAP 워크스페이스 형식의 posed 이미지 셋
  ```
  data_dir/
  ├── images/       # 촬영 이미지
  └── sparse/0/     # COLMAP 카메라 포즈 + sparse 포인트
  ```
- **출력** (`examples/results/<scene>/`):
  | 산출물 | 설명 |
  |---|---|
  | `ckpts/ckpt_*.pt` | 학습된 가우시안 파라미터 체크포인트 |
  | `ply/point_cloud_*.ply` | 웹 뷰어(three.js 등)에서 쓸 수 있는 splat PLY (`--save-ply` 필요) |
  | `renders/val_step*.png` | 검증 시점 렌더링 이미지 |
  | `videos/traj_*.mp4` | 카메라 궤적 플라이스루 영상 |
  | `stats/*.json` | PSNR/SSIM/LPIPS 등 평가 지표 |

## 로컬 학습 결과

MipNeRF-360 `garden` 씬, 30,000 steps 학습 결과:

| PSNR | SSIM | LPIPS | 가우시안 수 | 렌더 속도 |
|---|---|---|---|---|
| 24.16 | 0.906 | 0.326 | 863,388 | ~0.006 s/frame (실시간) |

## 실행 방법

```bash
# 설치 (CUDA toolkit 필요)
pip install -e .
pip install -r examples/requirements.txt

# 학습 (default 전략, PLY 내보내기 포함)
python examples/simple_trainer.py default \
    --data-dir data/<scene> --result-dir results/<scene> \
    --save-ply

# 학습된 체크포인트 인터랙티브 뷰어
python examples/simple_viewer.py --ckpt results/<scene>/ckpts/ckpt_29999_rank0.pt

# 임의 시점 렌더링
python examples/sample_inference.py --ckpt <ckpt> --scene <scene> --output-dir <out>
```

기타 트레이너: `simple_trainer_2dgs.py`(2DGS — 표면/메시 복원에 유리), `simple_trainer.py mcmc`(MCMC 전략). 권장 플래그 ablation은 `EXPLORATION.md` 참고.

## 폴더 구조

```
3d_gsplat/
├── gsplat/          # 코어 라이브러리 (CUDA 래스터라이저, 밀도 전략, 압축 등)
├── examples/        # 학습/뷰어/추론 스크립트 + 로컬 학습 결과(results/)
├── profiling/       # 래스터라이저 속도·메모리 벤치마크 스크립트
├── RESEARCH.md      # 3DGS 최신 기법 리서치 노트
└── EXPLORATION.md   # 학습 플래그 ablation 결과 (PSNR/SSIM/LPIPS)
```
