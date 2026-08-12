# 3D Gaussian Splatting 최신 기법 및 오픈소스 정리 (2025–2026)

> 작성일: 2026-07-24
> 최근 리서치·오픈소스 생태계 동향 요약

---

## 0. 개요

3D Gaussian Splatting(3DGS)은 신경망(NeRF) 대신 **명시적(explicit) 3D 가우시안 프리미티브**로 장면을 표현하여, 고품질 novel view synthesis를 **실시간 렌더링** 속도로 달성한 기법입니다 (SIGGRAPH 2023, INRIA). 2025~2026년 사이에는 다음 방향으로 빠르게 발전했습니다.

- **품질/기하 정확도**: anti-aliasing, 2DGS 기반 표면·메시 복원
- **압축·표준화**: glTF 표준 편입, SPZ 등 압축 포맷
- **동적 장면 / SLAM**: 움직이는 물체·실시간 트래킹
- **Feed-forward / 생성형**: sparse-view에서 단일 패스 복원, diffusion prior 결합
- **대규모 학습**: 멀티 GPU 분산 학습

---

## 1. 핵심 오픈소스 라이브러리 / 도구

| 프로젝트 | 설명 | 링크 |
|---|---|---|
| **gsplat** | PyTorch 기반의 효율적·모듈형 3DGS 라이브러리. API가 단순하고 커스터마이징 용이. 사실상 연구·프로덕션 표준 백엔드 | [arXiv](https://arxiv.org/pdf/2409.06765) / GitHub `nerfstudio-project/gsplat` |
| **원조 3DGS** | INRIA 공식 구현 (`graphdeco-inria/gaussian-splatting`) | SIGGRAPH 2023 |
| **OpenSplat** | Windows/Mac/Linux 지원 프로덕션급 도구. **GPU 없이 CPU fallback** 가능 | [Cybergarden 정리](https://cybergarden.au/blog/7-cutting-edge-open-source-gaussian-splatting-tools-for-2026) |
| **Taichi 3DGS** | Taichi 기반 순수 Python 구현. 코드 단순성·크로스플랫폼 지향 | 동상 |
| **Spark (World Labs)** | Three.js용 3DGS 렌더러. GitHub 선정 2025 영향력 있는 라이브러리 | 동상 |
| **Babylon.js v8.0** | 웹 엔진 내 3DGS 렌더링 개선 | 동상 |
| **Nerfstudio** | 학습/뷰어 통합 프레임워크 (gsplat 통합) | nerfstudio 프로젝트 |

### 리서치 추적용 저장소
- **awesome-gaussians**: Arxiv 논문을 매일 자동 갱신 — [GitHub](https://github.com/longxiang-ai/awesome-gaussians)
- **Awesome-3DGS-Applications** (TPAMI 2026 survey): segmentation/editing/generation 응용 정리 — [GitHub](https://github.com/heshuting555/Awesome-3DGS-Applications)

---

## 2. 품질 · 기하 정확도

### Anti-Aliasing
- **Mip-Splatting**: 3D smoothing filter + 2D Mip filter로 서로 다른 샘플링 레이트에서의 aliasing 제거. — [Radiance Fields 해설](https://radiancefields.com/mip-splatting-anti-aliasing-for-gaussian-splatting)
- **Multi-Scale 3DGS**: 멀티스케일 anti-aliased 렌더링 — [arXiv 2311.17089](https://arxiv.org/pdf/2311.17089)
- **AA-2DGS (Anti-Aliased 2D Gaussian Splatting, 2025)**: 2DGS의 기하 이점을 유지하며 스케일 간 렌더링 품질 향상 — [arXiv 2506.11252](https://arxiv.org/abs/2506.11252)

### 표면 복원 / 메시 (2DGS)
- **2D Gaussian Splatting (SIGGRAPH 2024)**: 3D 볼류메트릭 대비 view-consistency·기하 정확도 우수. depth/normal 복원, 메시 추출에 유리 — [ACM](https://dl.acm.org/doi/10.1145/3641519.3657428)

---

## 3. 압축 · 표준화

- **glTF 표준 편입 (2025년 8월)**: Khronos가 `KHR_gaussian_splatting` 확장으로 3DGS를 glTF 생태계에 공식 추가 → 상호운용성 확보
- **SPZ (Splat Zip)**: Niantic이 MIT 라이선스로 공개. 고정소수점 양자화 + 컬럼 기반 정렬로 **약 90% 압축**
- **Splatwizard**: 3DGS 압축 벤치마크 툴킷 — [arXiv 2512.24742](https://arxiv.org/pdf/2512.24742)

---

## 4. 동적 장면 & SLAM (2025–2026)

정적 장면 가정을 벗어나 움직이는 물체·실시간 트래킹을 다루는 방향.

- **DynaGSLAM (WACV 2026)**: 동적 장면에서 온라인 GS 렌더링 + 트래킹 + 움직이는 물체의 모션 예측 + ego-motion 추정을 실시간으로 수행한 최초의 real-time GS-SLAM — [arXiv 2503.11979](https://arxiv.org/abs/2503.11979)
- **DG-SLAM**: 하이브리드 포즈 최적화로 강건한 동적 GS-SLAM — [OpenReview](https://openreview.net/forum?id=tGozvLTDY3)
- **DGS-SLAM**: 동적 환경 대응 — [arXiv 2411.10722](https://arxiv.org/pdf/2411.10722)
- **DyPho-SLAM**: 동적 환경 실시간 포토리얼리스틱 SLAM — [arXiv 2509.00741](https://arxiv.org/pdf/2509.00741)
- **LVD-GS**: 계층적 explicit-implicit 표현 협업 렌더링 — [arXiv 2510.22669](https://arxiv.org/pdf/2510.22669)
- **Gaussian-LIC**: LiDAR-Inertial-Camera 퓨전 실시간 SLAM — [arXiv 2404.06926](https://arxiv.org/pdf/2404.06926)

---

## 5. Feed-forward / 생성형 (Sparse-view)

여러 장 최적화 대신 **단일 패스 네트워크**로 가우시안 파라미터를 예측하거나 diffusion prior로 sparse-view를 보강.

- **MVSplat / pixelSplat 계열**: 소수 입력 뷰에서 딥네트워크로 가우시안 직접 예측 (feed-forward 패러다임의 기반)
- **Generative Sparse-View GS (GS-GS, CVPR 2025)**: 사전학습 image diffusion으로 뷰 일관성 반복 정제 — [CVF](https://openaccess.thecvf.com/content/CVPR2025/html/Kong_Generative_Sparse-View_Gaussian_Splatting_CVPR_2025_paper.html)
- **ProSplat**: 1-step diffusion 정제 + reference-view 조건화 + epipolar attention으로 wide-baseline 개선
- **AnchorSplat**: 3D 기하 prior 결합 feed-forward — [arXiv 2604.07053](https://arxiv.org/pdf/2604.07053)
- **F4Splat**: feed-forward predictive densification — [arXiv 2603.21304](https://arxiv.org/pdf/2603.21304)
- **AD-GS**: sparse-input용 alternating densification — [arXiv 2509.11003](https://arxiv.org/pdf/2509.11003)
- 개념 정리: [Feed-forward 3DGS (Emergent Mind)](https://www.emergentmind.com/topics/feed-forward-3d-gaussian-splatting-3dgs)

---

## 6. 대규모 학습 & 편집

- **Grendel-GS (ICLR 2025)**: 분산 학습 시스템 — 멀티 GPU로 초대형 장면 스케일링
- **Instruct-4DGS (CVPR 2025)**: 4D 가우시안 기반 static-dynamic 분리로 효율적 동적 장면 편집
- **ROS 기반 온라인 3DGS 최적화 시스템**: 유연한 프론트엔드 통합 + 실시간 리파인 — [PMC](https://pmc.ncbi.nlm.nih.gov/articles/PMC12252524/)

---

## 7. 추천 시작 스택

1. **학습/실험**: `nerfstudio` + `gsplat` (PyTorch)
2. **데이터 준비**: COLMAP (SfM) 또는 폰 캡처
3. **품질 개선**: Mip-Splatting(안티에일리어싱), 2DGS(메시 필요 시)
4. **배포/뷰어**: Spark(Three.js) 또는 Babylon.js, 포맷은 SPZ / glTF
5. **동적·로봇**: DynaGSLAM 등 GS-SLAM 계열

---

## 참고 자료
- [7 Cutting-Edge Open-Source Gaussian Splatting Tools for 2026 (Cybergarden)](https://cybergarden.au/blog/7-cutting-edge-open-source-gaussian-splatting-tools-for-2026)
- [gsplat 논문 (arXiv)](https://arxiv.org/pdf/2409.06765)
- [Gaussian Splatting Year End Wrap Up (Radiance Fields)](https://radiancefields.substack.com/p/gaussian-splatting-year-end-wrap)
- [Recent Advances in 3D Gaussian Splatting (arXiv 2403.11134)](https://arxiv.org/pdf/2403.11134)
- [awesome-gaussians (GitHub)](https://github.com/longxiang-ai/awesome-gaussians)
- [Gaussian splatting: 2026 student guide (Medium)](https://medium.com/@Jamesroha/gaussian-splatting-a-complete-student-guide-to-3d-capture-in-2026-1195a6265870)
