def main():
    w, h, max_iter = 240, 160, 100
    inside = 0
    for py in range(h):
        for px in range(w):
            cr = px * 3.0 / w - 2.0
            ci = py * 2.0 / h - 1.0
            zr = 0.0
            zi = 0.0
            it = 0
            while it < max_iter and zr * zr + zi * zi <= 4.0:
                t = zr * zr - zi * zi + cr
                zi = 2.0 * zr * zi + ci
                zr = t
                it += 1
            if it == max_iter:
                inside += 1
    print(inside)
main()
