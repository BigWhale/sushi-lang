package main

import "fmt"

func main() {
	n := int32(160)
	a := make([]float64, n*n)
	b := make([]float64, n*n)
	c := make([]float64, n*n)
	for i := int32(0); i < n; i++ {
		for j := int32(0); j < n; j++ {
			a[i*n+j] = float64((i * j) % 7)
			b[i*n+j] = float64((i + j) % 5)
		}
	}
	for i := int32(0); i < n; i++ {
		for k := int32(0); k < n; k++ {
			aik := a[i*n+k]
			for j := int32(0); j < n; j++ {
				c[i*n+j] = c[i*n+j] + aik*b[k*n+j]
			}
		}
	}
	total := 0.0
	for i := int32(0); i < n*n; i++ {
		total += c[i]
	}
	fmt.Println(int64(total))
}
