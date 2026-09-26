package todo

import "errors"

// ErrEmptyTitle 表示标题无有效内容。 ErrEmptyTitle reports a blank title.
var ErrEmptyTitle = errors.New("title must not be empty")

// Service 管理单线程示例状态。 Service owns state for this single-threaded demo.
type Service struct {
	items []Item
}

// Create 校验标题并分配连续 ID。 Create validates a title and assigns a sequential ID.
func (s *Service) Create(title string) (Item, error) {
	if title == "" {
		return Item{}, ErrEmptyTitle
	}
	item := Item{ID: len(s.items) + 1, Title: title}
	s.items = append(s.items, item)
	return item, nil
}

// List 返回副本，避免调用者修改内部状态。 List returns a copy to protect internal state.
func (s *Service) List() []Item {
	return append([]Item(nil), s.items...)
}
