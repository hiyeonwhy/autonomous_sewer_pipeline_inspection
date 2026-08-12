from __future__ import annotations

import sys
from pathlib import Path

# 패키지 루트를 경로에 넣습니다. 파일을 직접 실행할 때 필요합니다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import math
from dataclasses import dataclass

import dataset  # noqa: E402
from analysis.profile import pearson, stdev  # noqa: E402

Matrix = list[list[float]]

# 카이저 기준: 상관행렬 PCA에서 고유값 1 미만인 성분은 원래 지표 하나보다도
# 설명력이 낮으므로 버립니다. 표준화하면 각 지표의 분산이 1이기 때문입니다.
KAISER_THRESHOLD = 1.0

# "실질 차원"을 판정할 누적 설명분산. 90%면 나머지 10%는 잡음으로 봅니다.
EFFECTIVE_VARIANCE = 0.90

JACOBI_MAX_SWEEPS = 100
JACOBI_TOLERANCE = 1e-12


def jacobi_eigen(matrix: Matrix) -> tuple[list[float], Matrix]:
    # 대칭행렬 고유값 분해(순환 야코비 회전). 비대각 원소를 하나씩 0으로 만드는
    # 회전을 반복하면 대각에 고유값이, 누적 회전행렬에 고유벡터가 남습니다.
    # 상관행렬은 항상 대칭이므로 일반 QR 알고리즘이 필요하지 않습니다.
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        raise ValueError("정방행렬이 아닙니다.")
    for row in range(size):
        for col in range(row + 1, size):
            if abs(matrix[row][col] - matrix[col][row]) > 1e-9:
                raise ValueError(f"대칭행렬이 아닙니다 ({row},{col}).")

    work = [list(row) for row in matrix]
    vectors = [[1.0 if i == j else 0.0 for j in range(size)] for i in range(size)]

    for _ in range(JACOBI_MAX_SWEEPS):
        off_diagonal = sum(
            work[i][j] ** 2 for i in range(size) for j in range(i + 1, size)
        )
        if off_diagonal < JACOBI_TOLERANCE:
            break

        for p in range(size - 1):
            for q in range(p + 1, size):
                if abs(work[p][q]) < 1e-18:
                    continue

                # 회전각. theta 를 직접 구하지 않고 tan 을 안정적으로 계산합니다.
                theta = (work[q][q] - work[p][p]) / (2.0 * work[p][q])
                sign = 1.0 if theta >= 0 else -1.0
                tangent = sign / (abs(theta) + math.sqrt(theta * theta + 1.0))
                cosine = 1.0 / math.sqrt(tangent * tangent + 1.0)
                sine = tangent * cosine

                for k in range(size):
                    a_kp = work[k][p]
                    a_kq = work[k][q]
                    work[k][p] = cosine * a_kp - sine * a_kq
                    work[k][q] = sine * a_kp + cosine * a_kq
                for k in range(size):
                    a_pk = work[p][k]
                    a_qk = work[q][k]
                    work[p][k] = cosine * a_pk - sine * a_qk
                    work[q][k] = sine * a_pk + cosine * a_qk
                for k in range(size):
                    v_kp = vectors[k][p]
                    v_kq = vectors[k][q]
                    vectors[k][p] = cosine * v_kp - sine * v_kq
                    vectors[k][q] = sine * v_kp + cosine * v_kq

    values = [work[i][i] for i in range(size)]

    # 고유값 내림차순으로 정렬합니다. 부호는 임의라 첫 성분이 양수가 되도록
    # 맞춰 실행마다 같은 결과가 나오게 합니다.
    order = sorted(range(size), key=lambda i: -values[i])
    sorted_values = [values[i] for i in order]
    sorted_vectors = []
    for row in range(size):
        sorted_vectors.append([vectors[row][i] for i in order])

    for col in range(size):
        first_nonzero = next(
            (sorted_vectors[row][col] for row in range(size)
             if abs(sorted_vectors[row][col]) > 1e-12),
            1.0,
        )
        if first_nonzero < 0:
            for row in range(size):
                sorted_vectors[row][col] = -sorted_vectors[row][col]

    return sorted_values, sorted_vectors


@dataclass
class PCAResult:
    names: list[str]
    sample_size: int
    dropped_all_missing: list[str]
    eigenvalues: list[float]
    eigenvectors: Matrix
    correlation: Matrix

    @property
    def total_variance(self) -> float:
        return sum(self.eigenvalues)

    @property
    def explained(self) -> list[float]:
        total = self.total_variance
        return [value / total for value in self.eigenvalues] if total else []

    @property
    def cumulative(self) -> list[float]:
        running = 0.0
        result = []
        for share in self.explained:
            running += share
            result.append(running)
        return result

    @property
    def kaiser_count(self) -> int:
        return sum(1 for value in self.eigenvalues if value > KAISER_THRESHOLD)

    @property
    def effective_dimension(self) -> int:
        for index, share in enumerate(self.cumulative, start=1):
            if share >= EFFECTIVE_VARIANCE:
                return index
        return len(self.eigenvalues)

    def loading(self, indicator: int, component: int) -> float:
        # 적재량 = 고유벡터 성분 × sqrt(고유값). 표준화 데이터에서는 지표와
        # 주성분의 상관계수와 같아서 해석하기 쉽습니다.
        value = self.eigenvalues[component]
        scale = math.sqrt(value) if value > 0 else 0.0
        return self.eigenvectors[indicator][component] * scale

    def to_dict(self) -> dict:
        return {
            "names": self.names,
            "sample_size": self.sample_size,
            "dropped_all_missing": self.dropped_all_missing,
            "eigenvalues": [round(value, 6) for value in self.eigenvalues],
            "explained": [round(value, 6) for value in self.explained],
            "cumulative": [round(value, 6) for value in self.cumulative],
            "kaiser_count": self.kaiser_count,
            "effective_dimension": self.effective_dimension,
            "loadings": {
                name: [
                    round(self.loading(index, component), 6)
                    for component in range(len(self.eigenvalues))
                ]
                for index, name in enumerate(self.names)
            },
            "summary": self.summary,
        }

    @property
    def summary(self) -> str:
        return (
            f"지표 {len(self.names)}개 (완전관측 {self.sample_size}개 구역) → "
            f"누적 설명분산 {EFFECTIVE_VARIANCE:.0%}에 필요한 성분 "
            f"{self.effective_dimension}개, 카이저 기준 {self.kaiser_count}개. "
            f"제1성분이 {self.explained[0]:.1%}를 설명합니다."
        )

    @property
    def verdict(self) -> str:
        count = len(self.names)
        effective = self.effective_dimension
        lines = []

        if effective < count:
            lines.append(
                f"지표 {count}개가 실질적으로는 {effective}개 축입니다. "
                f"{count - effective}개 분량의 정보가 중복입니다."
            )
        else:
            lines.append(
                f"지표 {count}개가 모두 필요합니다. 중복이 크지 않아 하나를 빼면 "
                "정보가 실제로 사라집니다."
            )

        if self.explained[0] >= 0.5:
            lines.append(
                f"제1성분이 {self.explained[0]:.1%}를 설명해 지표 대부분이 "
                "한 방향(도시 노후 인프라)을 함께 가리킵니다."
            )
        else:
            lines.append(
                f"제1성분이 {self.explained[0]:.1%}뿐이라 지표들이 서로 다른 "
                "측면을 재고 있습니다."
            )

        lines.append(
            "PCA는 '몇 개 축인가'까지만 답합니다. 어느 지표를 뺄지는 "
            "인과 논리로 정해야 합니다."
        )
        if self.dropped_all_missing:
            lines.append(
                f"전부 결측이라 제외한 지표: {self.dropped_all_missing}."
            )
        return " ".join(lines)


def standardized_columns(
    rows: list[dict], names: list[str]
) -> list[list[float]]:
    columns = []
    for name in names:
        values = [row[name] for row in rows]
        mean = sum(values) / len(values)
        deviation = stdev(values)
        if deviation < 1e-12:
            deviation = 1.0
        columns.append([(value - mean) / deviation for value in values])
    return columns


def analyze(rows: list[dict], names: list[str] | None = None) -> PCAResult:
    names = list(names or dataset.INDICATORS)

    # 전부 결측인 지표는 상관이 정의되지 않습니다. 남겨 두면 고유값 0이 하나
    # 생겨 설명분산 비율이 왜곡되므로 먼저 제외합니다.
    available = dataset.available_indicators(rows, names)
    dropped = [name for name in names if name not in available]
    if len(available) < 2:
        raise ValueError(f"관측된 지표가 {len(available)}개뿐입니다. PCA를 할 수 없습니다.")

    # 완전관측 행만 씁니다. 쌍별 완전관측으로 만든 상관행렬은 양의 준정부호가
    # 아닐 수 있고, 그러면 고유값에 음수가 나와 설명분산 해석이 깨집니다.
    complete = dataset.complete_rows(rows, available)
    if len(complete) <= len(available):
        raise ValueError(
            f"완전관측 행이 {len(complete)}개뿐입니다 (지표 {len(available)}개). "
            "PCA에 필요한 관측이 부족합니다."
        )

    columns = standardized_columns(complete, available)
    size = len(available)
    correlation = [
        [1.0 if i == j else pearson(columns[i], columns[j]) for j in range(size)]
        for i in range(size)
    ]

    values, vectors = jacobi_eigen(correlation)

    # 수치오차로 아주 작은 음수가 나올 수 있습니다. 0으로 눌러 설명분산 비율이
    # 음수가 되는 것을 막습니다.
    values = [max(value, 0.0) for value in values]

    return PCAResult(
        names=available,
        sample_size=len(complete),
        dropped_all_missing=dropped,
        eigenvalues=values,
        eigenvectors=vectors,
        correlation=correlation,
    )


def scree_table(result: PCAResult) -> str:
    lines = [f"  {'성분':>4} {'고유값':>8} {'설명분산':>9} {'누적':>8}", "  " + "-" * 46]
    for index, (value, share, cumulative) in enumerate(
        zip(result.eigenvalues, result.explained, result.cumulative), start=1
    ):
        bar = "█" * int(share * 30)
        mark = "  ← 카이저 기준" if value > KAISER_THRESHOLD else ""
        lines.append(
            f"  PC{index:<3} {value:8.3f} {share:9.1%} {cumulative:8.1%} {bar}{mark}"
        )
    return "\n".join(lines)


def loadings_table(result: PCAResult, components: int = 3) -> str:
    components = min(components, len(result.eigenvalues))
    header = f"  {'지표':26}" + "".join(f"{'PC' + str(i + 1):>10}" for i in range(components))
    lines = [header, "  " + "-" * (26 + 10 * components)]

    for index, name in enumerate(result.names):
        cells = "".join(
            f"{result.loading(index, component):10.3f}" for component in range(components)
        )
        lines.append(f"  {name:26}{cells}")

    lines.append("")
    lines.append("  적재량은 지표와 주성분의 상관계수입니다. |값| >= 0.7 이면 그 성분을")
    lines.append("  주로 구성하는 지표입니다.")
    return "\n".join(lines)


def component_makeup(result: PCAResult, component: int, threshold: float = 0.7) -> list[str]:
    return [
        name
        for index, name in enumerate(result.names)
        if abs(result.loading(index, component)) >= threshold
    ]


# ---------------------------------------------------------------- 자체 검증


def _rows(columns: dict[str, list]) -> list[dict]:
    count = len(next(iter(columns.values())))
    return [
        {"sgg_code": f"{10000 + i}", "full_name": f"T{i}",
         **{name: column[i] for name, column in columns.items()}}
        for i in range(count)
    ]


def _lcg(seed: int):
    state = seed

    def nxt() -> float:
        nonlocal state
        state = (state * 1103515245 + 12345) % (2**31)
        return state / (2**31)

    return nxt


def _self_test() -> int:
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        if condition:
            print(f"  [OK  ] {label}")
        else:
            failures += 1
            print(f"  [FAIL] {label}  {detail}")

    print("야코비 고유값 분해 - 해석적으로 답을 아는 경우")
    values, vectors = jacobi_eigen([[1.0, 0.0], [0.0, 1.0]])
    check("단위행렬 고유값 전부 1", all(abs(v - 1.0) < 1e-12 for v in values), str(values))

    # [[2,1],[1,2]] 의 고유값은 3, 1 (해석적으로 알려진 값)
    values, vectors = jacobi_eigen([[2.0, 1.0], [1.0, 2.0]])
    check("고유값 3, 1", abs(values[0] - 3.0) < 1e-12 and abs(values[1] - 1.0) < 1e-12,
          str(values))
    check("내림차순 정렬", values == sorted(values, reverse=True), str(values))

    # 대각행렬은 고유값이 대각 원소
    values, _ = jacobi_eigen([[5.0, 0.0, 0.0], [0.0, 3.0, 0.0], [0.0, 0.0, 7.0]])
    check("대각행렬 고유값 = 대각 원소 (내림차순)",
          [round(v, 9) for v in values] == [7.0, 5.0, 3.0], str(values))

    print()
    print("야코비 고유값 분해 - 불변량")
    target = [
        [1.0, 0.6, 0.3, 0.1],
        [0.6, 1.0, 0.5, 0.2],
        [0.3, 0.5, 1.0, 0.4],
        [0.1, 0.2, 0.4, 1.0],
    ]
    values, vectors = jacobi_eigen(target)
    trace = sum(target[i][i] for i in range(4))
    check("고유값 합 = 대각합", abs(sum(values) - trace) < 1e-10,
          f"{sum(values)} vs {trace}")
    check("고유값 전부 양수 (양정부호 상관행렬)", all(v > 0 for v in values), str(values))

    # 고유벡터 직교정규성
    orthonormal = True
    for i in range(4):
        norm = sum(vectors[k][i] ** 2 for k in range(4))
        if abs(norm - 1.0) > 1e-9:
            orthonormal = False
        for j in range(i + 1, 4):
            dot = sum(vectors[k][i] * vectors[k][j] for k in range(4))
            if abs(dot) > 1e-9:
                orthonormal = False
    check("고유벡터가 직교정규", orthonormal)

    # 재구성: A ≈ V Λ Vᵀ
    reconstruction_error = 0.0
    for i in range(4):
        for j in range(4):
            rebuilt = sum(
                vectors[i][k] * values[k] * vectors[j][k] for k in range(4)
            )
            reconstruction_error = max(reconstruction_error, abs(rebuilt - target[i][j]))
    check("A = V Λ Vᵀ 재구성", reconstruction_error < 1e-10, str(reconstruction_error))

    # 고유방정식 A v = λ v
    residual = 0.0
    for k in range(4):
        for i in range(4):
            left = sum(target[i][j] * vectors[j][k] for j in range(4))
            residual = max(residual, abs(left - values[k] * vectors[i][k]))
    check("A v = λ v 성립", residual < 1e-10, str(residual))

    print()
    print("야코비 고유값 분해 - 경계와 예외")
    values, _ = jacobi_eigen([[1.0, 1.0], [1.0, 1.0]])
    check("완전 중복 열은 고유값 0을 만듦", abs(values[1]) < 1e-12, str(values))
    check("완전 중복 열의 첫 고유값 2", abs(values[0] - 2.0) < 1e-12, str(values))

    try:
        jacobi_eigen([[1.0, 2.0], [3.0, 4.0]])
        check("비대칭은 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("비대칭은 예외", True)

    try:
        jacobi_eigen([[1.0, 2.0]])
        check("비정방은 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("비정방은 예외", True)

    single, _ = jacobi_eigen([[4.0]])
    check("1×1 처리", single == [4.0], str(single))

    print()
    print("결정성 (실행마다 같은 결과)")
    first = jacobi_eigen(target)
    second = jacobi_eigen(target)
    check("고유값 동일", first[0] == second[0])
    check("고유벡터 부호까지 동일", first[1] == second[1])

    print()
    print("PCA - 구조를 아는 합성 데이터")
    # a 와 b 는 완전 중복, c 는 독립. 실질 차원 2개여야 합니다.
    nxt = _lcg(4242)
    base = [nxt() for _ in range(60)]
    independent = [nxt() for _ in range(60)]
    duplicated = _rows(
        {
            "aged_ratio_25y": base,
            "concrete_ratio": [value * 2.0 for value in base],
            "combined_sewer_ratio": independent,
        }
    )
    result = analyze(
        duplicated, ["aged_ratio_25y", "concrete_ratio", "combined_sewer_ratio"]
    )
    check("표본 수 기록", result.sample_size == 60, str(result.sample_size))
    check("고유값 3개", len(result.eigenvalues) == 3, str(len(result.eigenvalues)))
    check("중복 열이 있으면 마지막 고유값 ~0", result.eigenvalues[-1] < 1e-9,
          str(result.eigenvalues[-1]))
    check("고유값 합 = 지표 수 (상관행렬)", abs(result.total_variance - 3.0) < 1e-9,
          str(result.total_variance))
    check("누적 설명분산 마지막은 1", abs(result.cumulative[-1] - 1.0) < 1e-12,
          str(result.cumulative[-1]))
    check("실질 차원 2개", result.effective_dimension == 2, str(result.effective_dimension))
    check("설명분산 내림차순", result.explained == sorted(result.explained, reverse=True))
    check(
        "중복 지표가 같은 성분에 실림",
        abs(abs(result.loading(0, 0)) - abs(result.loading(1, 0))) < 1e-6,
        f"{result.loading(0, 0)} vs {result.loading(1, 0)}",
    )
    check("판정문이 중복을 지적", "중복" in result.verdict, result.verdict[:60])

    print()
    print("PCA - 독립 지표만 있는 경우")
    nxt = _lcg(999)
    independent_rows = _rows(
        {
            "aged_ratio_25y": [nxt() for _ in range(80)],
            "concrete_ratio": [nxt() for _ in range(80)],
            "combined_sewer_ratio": [nxt() for _ in range(80)],
        }
    )
    plain = analyze(
        independent_rows,
        ["aged_ratio_25y", "concrete_ratio", "combined_sewer_ratio"],
    )
    check("독립이면 고유값이 1 근처", all(0.6 < v < 1.5 for v in plain.eigenvalues),
          str([round(v, 3) for v in plain.eigenvalues]))
    check("독립이면 실질 차원이 지표 수에 가까움", plain.effective_dimension >= 2,
          str(plain.effective_dimension))

    print()
    print("PCA - 결측 처리")
    with_missing = _rows(
        {
            "aged_ratio_25y": [0.1, 0.2, 0.3, None, 0.5, 0.6, 0.7, 0.8],
            "concrete_ratio": [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
            "sewer_subsidence_per_km": [None] * 8,
        }
    )
    missing_result = analyze(
        with_missing,
        ["aged_ratio_25y", "concrete_ratio", "sewer_subsidence_per_km"],
    )
    check(
        "전부 결측 지표 제외",
        missing_result.dropped_all_missing == ["sewer_subsidence_per_km"],
        str(missing_result.dropped_all_missing),
    )
    check("완전관측 행만 사용", missing_result.sample_size == 7,
          str(missing_result.sample_size))
    check("남은 지표로 계산", missing_result.names == ["aged_ratio_25y", "concrete_ratio"],
          str(missing_result.names))
    check("판정문에 제외 지표 명시", "전부 결측" in missing_result.verdict)

    print()
    print("PCA - 경계 조건")
    try:
        analyze(_rows({"aged_ratio_25y": [None, None, None]}), ["aged_ratio_25y"])
        check("관측 지표 부족은 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("관측 지표 부족은 예외", True)

    try:
        analyze(
            _rows({"aged_ratio_25y": [0.1, 0.2], "concrete_ratio": [0.3, 0.4]}),
            ["aged_ratio_25y", "concrete_ratio"],
        )
        check("표본이 지표 수 이하면 예외", False, "예외가 안 났습니다")
    except ValueError:
        check("표본이 지표 수 이하면 예외", True)

    print()
    print("표 렌더링·직렬화")
    check("scree_table", bool(scree_table(result)))
    check("loadings_table", bool(loadings_table(result)))
    check("성분 구성 조회", isinstance(component_makeup(result, 0), list))
    try:
        json.dumps(result.to_dict(), ensure_ascii=False)
        check("JSON 직렬화 가능", True)
    except (TypeError, ValueError) as error:
        check("JSON 직렬화 가능", False, str(error))

    print()
    print("표준 구현 대조 (numpy, 없으면 건너뜀)")
    try:
        import numpy
    except ImportError:
        print("  numpy 가 없어 건너뜁니다. pip install numpy 로 대조할 수 있습니다.")
    else:
        reference = sorted(numpy.linalg.eigvalsh(numpy.array(target)), reverse=True)
        ours, _ = jacobi_eigen(target)
        gap = max(abs(a - b) for a, b in zip(ours, reference))
        check("numpy eigvalsh 와 일치 (1e-10)", gap < 1e-10, str(gap))

    print()
    if failures:
        print(f"실패 {failures}건")
    else:
        print("전부 통과")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
