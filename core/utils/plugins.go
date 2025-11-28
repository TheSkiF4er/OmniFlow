package utils

import (
	"os/exec"
)

// RunPlugin executes a plugin as an external process
func RunPlugin(path string, args ...string) ([]byte, error) {
	cmd := exec.Command(path, args...)
	output, err := cmd.CombinedOutput()
	return output, err
}
