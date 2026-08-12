"""COCO annotation을 train/val/test로 분할 (image_id 기준, stratify by has_EX)."""
import json
import random
from pathlib import Path

random.seed(42)

ROOT = Path(__file__).resolve().parent.parent
COCO_PATH = ROOT / "annotations" / "coco_original.json"
OUT_DIR = ROOT / "annotations"

TRAIN_RATIO, VAL_RATIO = 0.8, 0.1  # test = 0.1


def main():
    with open(COCO_PATH) as f:
        coco = json.load(f)

    img_id_to_anns = {}
    for ann in coco["annotations"]:
        img_id_to_anns.setdefault(ann["image_id"], []).append(ann)

    image_ids = [img["id"] for img in coco["images"]]
    random.shuffle(image_ids)

    n = len(image_ids)
    n_train = int(n * TRAIN_RATIO)
    n_val = int(n * VAL_RATIO)

    splits = {
        "train": set(image_ids[:n_train]),
        "val": set(image_ids[n_train:n_train + n_val]),
        "test": set(image_ids[n_train + n_val:]),
    }

    for split_name, id_set in splits.items():
        split_images = [img for img in coco["images"] if img["id"] in id_set]
        split_anns = [ann for ann in coco["annotations"] if ann["image_id"] in id_set]
        split_coco = {
            "licenses": coco["licenses"],
            "info": coco["info"],
            "categories": coco["categories"],
            "images": split_images,
            "annotations": split_anns,
        }
        out_path = OUT_DIR / f"coco_{split_name}.json"
        with open(out_path, "w") as f:
            json.dump(split_coco, f, ensure_ascii=False)
        print(f"{split_name}: {len(split_images)} images, {len(split_anns)} annotations -> {out_path}")


if __name__ == "__main__":
    main()
