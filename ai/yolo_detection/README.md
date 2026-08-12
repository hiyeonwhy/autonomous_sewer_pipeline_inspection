# YOLO Detection — 관로 내부 결함(균열) 탐지

하수관로 내부 CCTV 이미지에서 결함(균열)을 자동 탐지·세그멘테이션하는 모듈입니다. **YOLO11n**(detection)과 **YOLO11n-seg**(segmentation)를 파인튜닝했으며, 세그멘테이션 라벨은 **SAM 2.1**로 bbox 기반 pseudo-label을 생성해 사용했습니다.

## 데이터셋

- 출처: AI학습데이터구축 — 하수관로 내부 이미지 (㈜태성에스엔아이, COCO 포맷)
- 이미지: 640×360 PNG, 총 10,000장 / 어노테이션 10,868개
- 클래스 2개: `EX`(0), `CL`(1, 종방향 균열)
- 분할: train 8,000 / val 1,000 / test 1,000 (8:1:1, seed 42)


## 사용 모델

| 태스크 | 사전학습 가중치 | 파인튜닝 결과 |
|---|---|---|
| Detection | `yolo11n.pt` | `models/detect/crack_detect_best.pt` |
| Segmentation | `yolo11n-seg.pt` | `models/segment/crack_seg_best.pt` |
| Pseudo-labeling | `sam2.1_b.pt` (SAM 2.1 base) | bbox → 세그멘테이션 폴리곤 생성 (학습 X) |

## 파이프라인

```
coco_original.json
   │ analyze_dataset.py      # EDA (클래스 분포, bbox 크기)
   │ split_dataset.py        # 8:1:1 분할 → coco_{train,val,test}.json
   ├─ coco_to_yolo.py        # COCO → YOLO det txt + configs/yolo_detect.yaml
   └─ gen_sam_masks.py       # SAM2.1로 seg pseudo-label + configs/seg_train.yaml
   │
   ▼ ultralytics CLI 학습
yolo detect  train model=yolo11n.pt     data=configs/yolo_detect.yaml epochs=80 batch=16 imgsz=640
yolo segment train model=yolo11n-seg.pt data=configs/seg_train.yaml   epochs=80 batch=16 imgsz=640
   │
   ▼
runs/ (지표·시각화) → best.pt를 models/detect|segment/로 복사
```

`*_v2.py` 스크립트는 확장 데이터셋(`data/train|val` 직접 배치) 버전입니다.

## 폴더 구조

```
yolo_detection/
├── annotations/   # COCO JSON (original + train/val/test 분할)
├── configs/       # YOLO 데이터셋 yaml (det/seg, v1/v2)
├── scripts/       # EDA·분할·포맷 변환·SAM pseudo-labeling 스크립트 6개
├── models/        # 최종 배포용 best 가중치 (detect / segment)
├── runs/          # 학습 로그·지표·시각화 (results.csv, *.png, weights/)
└── venv/          # (git 제외)
```
