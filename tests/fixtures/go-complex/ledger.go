package ledger

import (
	"errors"
	"math"
)

// Transfer 原子计算余额，拒绝非法金额与溢出。 Transfer validates before changing either balance.
func Transfer(from, to, amount int64) (int64, int64, error) {
	if from < 0 || to < 0 || amount <= 0 || from < amount || to > math.MaxInt64-amount {
		return from, to, errors.New("invalid transfer")
	}
	return from - amount, to + amount, nil
}
