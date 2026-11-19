package main

import (
	"fmt"
	"math"
)

func main() {
	n := int64(2000000)
	acc := 0.0
	for i := int64(0); i < n; i++ {
		x := float64(i%1000)*0.001 + 0.5
		acc = acc + math.Sqrt(x) + math.Sin(x) + math.Cos(x) + math.Exp(x) + math.Log(x)
		acc = acc + math.Pow(x, 1.5) + math.Floor(x*10.0) + math.Atan2(x, 1.0) + math.Hypot(x, 2.0)
	}
	fmt.Println(int64(acc))
}
