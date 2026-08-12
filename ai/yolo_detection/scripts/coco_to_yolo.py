"""COCO bbox annotation -> YOLO detection 포맷(txt) 변환 + data.yaml 생성.

YOLO 디렉토리 레이아웃:
  data/yolo_det/images/{train,val,test}/*.png (symlink)
  data/yolo_det/labels/{train,val,test}/*.txt
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_IMG_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "yolo_det"

# COCO category_id -> YOLO class index (0-based)
CAT_ID_TO_IDX = {1: 0, 2: 1}  # EX=0, CL=1
CLASS_NAMES = ["EX", "CL"]


def convert_split(split_name: str):
    coco_path = ROOT / "annotations" / f"coco_{split_name}.json"
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

    for img_id, img in img_by_id.items():
        file_name = img["file_name"]
        w, h = img["width"], img["height"]

        src = RAW_IMG_DIR / file_name
        dst = img_dir / file_name
        if not dst.exists() and src.exists():
            dst.symlink_to(src.resolve())

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

    print(f"{split_name}: {len(img_by_id)} images converted -> {img_dir}, {lbl_dir}")


def write_data_yaml():
    yaml_path = ROOT / "configs" / "yolo_detect.yaml"
    content = f"""path: {OUT_DIR}
train: images/train
val: images/val
test: images/test
nc: {len(CLASS_NAMES)}
names: {CLASS_NAMES}
"""
    yaml_path.write_text(content)
    print(f"data yaml -> {yaml_path}")


if __name__ == "__main__":
    for split in ["train", "val", "test"]:
        convert_split(split)
    write_data_yaml()
