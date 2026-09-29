package main

import "fmt"

func main() {
	w, h, maxIter := int32(240), int32(160), int32(100)
	inside := int32(0)
	for py := int32(0); py < h; py++ {
		for px := int32(0); px < w; px++ {
			cr := float64(px)*3.0/float64(w) - 2.0
			ci := float64(py)*2.0/float64(h) - 1.0
			zr, zi := 0.0, 0.0
			it := int32(0)
			for it < maxIter && zr*zr+zi*zi <= 4.0 {
				t := zr*zr - zi*zi + cr
				zi = 2.0*zr*zi + ci
				zr = t
				it++
			}
			if it == maxIter {
				inside++
			}
		}
	}
	fmt.Println(inside)
}
