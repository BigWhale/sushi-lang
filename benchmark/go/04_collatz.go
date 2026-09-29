package main

import "fmt"

func main() {
	best, bestN := int32(0), int32(0)
	for start := int32(1); start < 300000; start++ {
		x := int64(start)
		steps := int32(0)
		for x != 1 {
			if x%2 == 0 {
				x = x / 2
			} else {
				x = 3*x + 1
			}
			steps++
		}
		if steps > best {
			best = steps
			bestN = start
		}
	}
	fmt.Println(bestN, best)
}
