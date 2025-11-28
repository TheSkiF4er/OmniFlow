package utils

import (
	"encoding/json"
	"os"
)

type Config struct {
	DatabaseURL string `json:"database_url"`
	Port        int    `json:"port"`
	LogLevel    string `json:"log_level"`
}

func LoadConfig(path string) (*Config, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()

	cfg := &Config{}
	decoder := json.NewDecoder(file)
	err = decoder.Decode(cfg)
	if err != nil {
		return nil, err
	}
	return cfg, nil
}
