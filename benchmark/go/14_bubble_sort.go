package main

import "fmt"

func main() {
	n := int32(2500)
	a := make([]int32, n)
	seed := int32(12345)
	for i := int32(0); i < n; i++ {
		seed = (seed*1103 + 12345) % 65536
		a[i] = seed
	}
	for i := int32(0); i < n; i++ {
		for j := int32(0); j < n-1-i; j++ {
			if a[j] > a[j+1] {
				a[j], a[j+1] = a[j+1], a[j]
			}
		}
	}
	fmt.Println(a[0], a[n/2], a[n-1])
}
