package utils

import "fmt"

type OmniError struct {
	Code    int
	Message string
}

func (e OmniError) Error() string {
	return fmt.Sprintf("Code %d: %s", e.Code, e.Message)
}

func NewOmniError(code int, msg string) error {
	return OmniError{Code: code, Message: msg}
}
