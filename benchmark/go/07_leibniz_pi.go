package main

import "fmt"

func main() {
	s, sign := 0.0, 1.0
	for k := int32(0); k < 20000000; k++ {
		s = s + sign/(2.0*float64(k)+1.0)
		sign = 0.0 - sign
	}
	fmt.Println(int64(s * 4.0 * 1000000000.0))
}
