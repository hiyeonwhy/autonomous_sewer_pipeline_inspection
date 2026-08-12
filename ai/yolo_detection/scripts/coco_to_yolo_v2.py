"""확장된 COCO 데이터(data/train_(...).json, data/val_(...).json) -> YOLO detection 포맷 변환.

원본 coco_to_yolo.py와 달리 data/raw 심볼릭 링크가 아니라
data/train, data/val 에 이미지가 직접 존재하는 신규 데이터셋을 사용한다.

YOLO 디렉토리 레이아웃:
  data/yolo_det_v2/images/{train,val}/*.png (symlink)
  data/yolo_det_v2/labels/{train,val}/*.txt
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "yolo_det_v2"

SPLITS = {
    "train": (ROOT / "data" / "train_(Crack-Longitudinal,CL).json", ROOT / "data" / "train"),
    "val": (ROOT / "data" / "val_(Crack-Longitudinal,CL).json", ROOT / "data" / "val"),
}

CAT_ID_TO_IDX = {1: 0, 2: 1}  # EX=0, CL=1
CLASS_NAMES = ["EX", "CL"]


def convert_split(split_name: str, coco_path: Path, img_src_dir: Path):
    with open(coco_path) as f:
        coco = json.load(f)

    img_dir = OUT_DIR / "images" / split_name
    lbl_dir = OUT_DIR / "labels" / split_name
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    img_by_id = {img["id"]: img for img in coco["images"]}
    anns_by_img = {}
    for ann in coco["annotations"]:
        anns_by_img.setdefault(ann["image_id"], []).append(ann)

    n_missing = 0
    for img_id, img in img_by_id.items():
        file_name = img["file_name"]
        w, h = img["width"], img["height"]

        src = img_src_dir / file_name
        dst = img_dir / file_name
        if not dst.exists():
            if src.exists():
                dst.symlink_to(src.resolve())
            else:
                n_missing += 1
                continue

        lines = []
        for ann in anns_by_img.get(img_id, []):
            x, y, bw, bh = ann["bbox"]
            cx = (x + bw / 2) / w
            cy = (y + bh / 2) / h
            nw = bw / w
            nh = bh / h
            cls_idx = CAT_ID_TO_IDX[ann["category_id"]]
            lines.append(f"{cls_idx} {cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}")

        lbl_path = lbl_dir / (Path(file_name).stem + ".txt")
        lbl_path.write_text("\n".join(lines))

    print(f"{split_name}: {len(img_by_id)} images converted -> {img_dir}, {lbl_dir} (missing src={n_missing})")


def write_data_yaml():
    yaml_path = ROOT / "configs" / "yolo_detect_v2.yaml"
    content = f"""path: {OUT_DIR}
train: images/train
val: images/val
nc: {len(CLASS_NAMES)}
names: {CLASS_NAMES}
"""
    yaml_path.write_text(content)
    print(f"data yaml -> {yaml_path}")


def main():
    for split_name, (coco_path, img_src_dir) in SPLITS.items():
        convert_split(split_name, coco_path, img_src_dir)
    write_data_yaml()


if __name__ == "__main__":
    main()
