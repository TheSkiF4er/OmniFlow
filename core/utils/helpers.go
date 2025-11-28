package utils

import (
	"time"
)

// SleepSeconds pauses execution for n seconds
func SleepSeconds(n int) {
	time.Sleep(time.Duration(n) * time.Second)
}

// Min returns the minimum of two integers
func Min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// Max returns the maximum of two integers
func Max(a, b int) int {
	if a > b {
		return a
	}
	return b
}
