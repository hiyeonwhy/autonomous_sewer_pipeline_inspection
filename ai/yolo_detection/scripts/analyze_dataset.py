"""데이터셋 EDA: 클래스 분포, bbox 크기 통계."""
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COCO_PATH = ROOT / "annotations" / "coco_original.json"


def main():
    with open(COCO_PATH) as f:
        coco = json.load(f)

    cats = {c["id"]: c["name"] for c in coco["categories"]}
    counts = {}
    areas = {}
    for ann in coco["annotations"]:
        name = cats[ann["category_id"]]
        counts[name] = counts.get(name, 0) + 1
        areas.setdefault(name, []).append(ann["area"])

    print(f"total images: {len(coco['images'])}")
    print(f"total annotations: {len(coco['annotations'])}")
    for name, cnt in counts.items():
        a = areas[name]
        print(f"  class={name}: count={cnt}, mean_area={statistics.mean(a):.1f}, "
              f"median_area={statistics.median(a):.1f}")


if __name__ == "__main__":
    main()
