package ledger

import (
	"math"
	"testing"
)

// TestBoundaries 覆盖金额边界和不变量。 TestBoundaries covers validation and balance invariants.
func TestBoundaries(t *testing.T) {
	for _, tc := range []struct {
		name             string
		from, to, amount int64
		fail             bool
	}{
		{"normal", 100, 20, 15, false},
		{"drain", 100, 20, 100, false},
		{"insufficient", 100, 20, 101, true},
		{"negative", 100, 20, -1, true},
		{"zero", 100, 20, 0, true},
		{"overflow", 100, math.MaxInt64, 1, true},
		{"negative account", -1, 20, 1, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			from, to, err := Transfer(tc.from, tc.to, tc.amount)
			if (err != nil) != tc.fail {
				t.Fatalf("unexpected error: %v", err)
			}
			if tc.fail {
				if from != tc.from || to != tc.to {
					t.Fatal("failed transfer changed balances")
				}
			} else if from != tc.from-tc.amount || to != tc.to+tc.amount {
				t.Fatal("successful transfer violated balance invariant")
			}
		})
	}
}

// TestBatchRollback 验证跨文件调用的失败回滚。 TestBatchRollback verifies rollback across files.
func TestBatchRollback(t *testing.T) {
	from, to, err := Batch(100, 5, []int64{40, 70})
	if err == nil || from != 100 || to != 5 {
		t.Fatal("batch must roll back an earlier valid transfer")
	}
	from, to, err = Batch(100, 5, []int64{40, 60})
	if err != nil || from != 0 || to != 105 {
		t.Fatal("valid batch failed")
	}
}
