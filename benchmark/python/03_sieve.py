def main():
    n = 5000000
    comp = [False] * n
    count = 0
    i = 2
    while i < n:
        if not comp[i]:
            count += 1
            if i <= n // i:
                j = i * i
                while j < n:
                    comp[j] = True
                    j += i
        i += 1
    print(count)
main()
