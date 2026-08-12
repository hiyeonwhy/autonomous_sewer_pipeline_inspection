from __future__ import annotations

Matrix = list[list[float]]
Vector = list[float]


class SingularMatrixError(ValueError):
    pass


def zeros(rows: int, cols: int) -> Matrix:
    return [[0.0] * cols for _ in range(rows)]


def identity(size: int) -> Matrix:
    return [
        [1.0 if row == col else 0.0 for col in range(size)]
        for row in range(size)
    ]


def transpose(matrix: Matrix) -> Matrix:
    return [list(column) for column in zip(*matrix)]


def matmul(left: Matrix, right: Matrix) -> Matrix:
    rows = len(left)
    inner = len(right)
    cols = len(right[0])

    if len(left[0]) != inner:
        raise ValueError(
            f"차원 불일치: ({rows}×{len(left[0])}) × ({inner}×{cols})"
        )

    right_t = transpose(right)
    return [
        [sum(a * b for a, b in zip(left_row, right_col)) for right_col in right_t]
        for left_row in left
    ]


def matvec(matrix: Matrix, vector: Vector) -> Vector:
    if len(matrix[0]) != len(vector):
        raise ValueError(f"차원 불일치: ({len(matrix)}×{len(matrix[0])}) × {len(vector)}")
    return [sum(a * b for a, b in zip(row, vector)) for row in matrix]


def xtwx(design: Matrix, weights: Vector) -> Matrix:
    rows = len(design)
    cols = len(design[0])
    result = zeros(cols, cols)

    for i in range(rows):
        weight = weights[i]
        if weight == 0.0:
            continue
        row = design[i]
        for j in range(cols):
            weighted = weight * row[j]
            if weighted == 0.0:
                continue
            for k in range(j, cols):
                result[j][k] += weighted * row[k]

    # 대칭 채우기
    for j in range(cols):
        for k in range(j + 1, cols):
            result[k][j] = result[j][k]

    return result


def xtwy(design: Matrix, weights: Vector, target: Vector) -> Vector:
    cols = len(design[0])
    result = [0.0] * cols

    for i, row in enumerate(design):
        factor = weights[i] * target[i]
        if factor == 0.0:
            continue
        for j in range(cols):
            result[j] += factor * row[j]

    return result


def solve(matrix: Matrix, vector: Vector) -> Vector:
    size = len(matrix)
    if len(vector) != size:
        raise ValueError(f"차원 불일치: {size}×{len(matrix[0])} 와 {len(vector)}")

    # 원본 훼손을 막기 위해 증강행렬을 복사합니다.
    augmented = [list(row) + [vector[index]] for index, row in enumerate(matrix)]

    for column in range(size):
        # 부분 피벗: 절댓값이 가장 큰 행을 올립니다.
        pivot_row = max(
            range(column, size),
            key=lambda row: abs(augmented[row][column]),
        )
        if abs(augmented[pivot_row][column]) < 1e-13:
            raise SingularMatrixError(
                f"특이행렬입니다 (열 {column}). 설명변수 간 완전 상관을 확인하세요."
            )

        if pivot_row != column:
            augmented[column], augmented[pivot_row] = (
                augmented[pivot_row],
                augmented[column],
            )

        pivot = augmented[column][column]
        for row in range(column + 1, size):
            factor = augmented[row][column] / pivot
            if factor == 0.0:
                continue
            for col in range(column, size + 1):
                augmented[row][col] -= factor * augmented[column][col]

    # 후진 대입
    solution = [0.0] * size
    for row in reversed(range(size)):
        total = augmented[row][size]
        for col in range(row + 1, size):
            total -= augmented[row][col] * solution[col]
        solution[row] = total / augmented[row][row]

    return solution


def inverse(matrix: Matrix) -> Matrix:
    size = len(matrix)
    columns = []

    for index in range(size):
        basis = [1.0 if row == index else 0.0 for row in range(size)]
        columns.append(solve(matrix, basis))

    return transpose(columns)


def add_ridge(matrix: Matrix, penalty: float) -> Matrix:
    result = [list(row) for row in matrix]
    for index in range(1, len(result)):
        result[index][index] += penalty
    return result


def standardize(columns: list[Vector]) -> tuple[list[Vector], list[float], list[float]]:
    scaled: list[Vector] = []
    means: list[float] = []
    deviations: list[float] = []

    for column in columns:
        count = len(column)
        mean = sum(column) / count
        variance = sum((value - mean) ** 2 for value in column) / max(count - 1, 1)
        deviation = variance**0.5

        # 상수 열은 표준화할 수 없으므로 그대로 둡니다.
        if deviation < 1e-12:
            deviation = 1.0

        scaled.append([(value - mean) / deviation for value in column])
        means.append(mean)
        deviations.append(deviation)

    return scaled, means, deviations
