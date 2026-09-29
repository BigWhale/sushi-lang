package main

import (
	"fmt"
	"math"
)

type Point struct{ x, y float64 }

func main() {
	n := int32(1500)
	pts := make([]Point, n)
	for i := int32(0); i < n; i++ {
		pts[i] = Point{float64((i * 37) % 1000), float64((i * 91) % 1000)}
	}
	total := 0.0
	for i := int32(0); i < n; i++ {
		xi, yi := pts[i].x, pts[i].y
		for j := i + 1; j < n; j++ {
			dx := xi - pts[j].x
			dy := yi - pts[j].y
			total += math.Sqrt(dx*dx + dy*dy)
		}
	}
	fmt.Println(int64(total))
}
