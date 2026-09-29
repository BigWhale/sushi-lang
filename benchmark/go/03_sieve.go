package main

import "fmt"

func main() {
	n := int32(5000000)
	comp := make([]bool, n)
	count := int32(0)
	for i := int32(2); i < n; i++ {
		if !comp[i] {
			count++
			if i <= n/i {
				for j := i * i; j < n; j += i {
					comp[j] = true
				}
			}
		}
	}
	fmt.Println(count)
}
