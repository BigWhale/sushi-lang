package main

import "fmt"

func solve(cols []int32, row, n int32) int32 {
	if row == n {
		return 1
	}
	count := int32(0)
	for c := int32(0); c < n; c++ {
		ok := true
		for r := int32(0); ok && r < row; r++ {
			d := cols[r] - c
			if d == 0 || d == row-r || d == r-row {
				ok = false
			}
		}
		if ok {
			cols[row] = c
			count += solve(cols, row+1, n)
		}
	}
	return count
}

func main() {
	n := int32(9)
	fmt.Println(solve(make([]int32, n), 0, n))
}
