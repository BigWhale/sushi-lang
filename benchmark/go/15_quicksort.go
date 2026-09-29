package main

import "fmt"

func qsort(a []int32, lo, hi int32) {
	if lo >= hi {
		return
	}
	p := a[(lo+hi)/2]
	i, j := lo, hi
	for i <= j {
		for a[i] < p {
			i++
		}
		for a[j] > p {
			j--
		}
		if i <= j {
			a[i], a[j] = a[j], a[i]
			i++
			j--
		}
	}
	qsort(a, lo, j)
	qsort(a, i, hi)
}

func main() {
	n := int32(500000)
	a := make([]int32, n)
	seed := int64(42)
	for i := int32(0); i < n; i++ {
		seed = (seed*1103515245 + 12345) % 2147483648
		a[i] = int32(seed % 1000000)
	}
	qsort(a, 0, n-1)
	var check int64
	for i := int32(0); i < 1000; i++ {
		check += int64(a[i*499])
	}
	fmt.Println(a[0], a[n-1], check)
}
