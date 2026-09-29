def solve(cols, row, n):
    if row == n:
        return 1
    count = 0
    for c in range(n):
        ok = True
        r = 0
        while ok and r < row:
            d = cols[r] - c
            if d == 0 or d == row - r or d == r - row:
                ok = False
            r += 1
        if ok:
            cols[row] = c
            count += solve(cols, row + 1, n)
    return count

n = 9
print(solve([0] * n, 0, n))
