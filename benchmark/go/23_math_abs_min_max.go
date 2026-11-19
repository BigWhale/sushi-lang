package main

import (
	"fmt"
	"math"
)

func abs64(v int64) int64 {
	if v < 0 {
		return -v
	}
	return v
}

func main() {
	n := int64(5000000)
	acc := int64(0)
	facc := 0.0
	for i := int64(0); i < n; i++ {
		a := (i*7919)%1000 - 500
		b := (i*104729)%1000 - 500
		acc = acc + abs64(a) + min(a, b) + max(a, b)
		x := float64(a) * 0.5
		y := float64(b) * 0.25
		facc = facc + math.Abs(x) + min(x, y) + max(x, y)
	}
	fmt.Printf("%d %d\n", acc, int64(facc))
}
