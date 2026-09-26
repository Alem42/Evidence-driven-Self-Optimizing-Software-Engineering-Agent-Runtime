package todo

// Item 表示内存中的待办事项。 Item represents an in-memory todo.
type Item struct {
	ID    int
	Title string
}

// NormalizeTitle 统一标题入口；此版本保留待修复缺陷。
// NormalizeTitle centralizes title handling; this version retains the task defect.
func NormalizeTitle(title string) string {
	return title
}
