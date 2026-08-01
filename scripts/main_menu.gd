extends Control
## 主界面（标题屏）。启动主场景 → 开始游戏切到世界场景；退出关闭游戏。
##
## 比赛展示导向：入场做标题淡入 + 卖点标签逐个亮起，
## 让评委在前 3 秒就抓到「AI 驱动的社交博弈」这个核心差异点。

@onready var start_button: Button = %StartButton
@onready var quit_button: Button = %QuitButton
@onready var title_box: VBoxContainer = %TitleBox
@onready var tag_row: HBoxContainer = %TagRow
@onready var hook_label: Label = %HookLabel

## 轮播的悬念文案：每隔几秒换一句，暗示 NPC 真的在背地里活动
const HOOKS := [
	"「苔老板刚才跟焰仔说了你的坏话。」",
	"「小翠记得你上次送的野花。」",
	"「老咸在河边等了你一整个下午。」",
	"「煊赫把你的秘密告诉了三个人。」",
	"「小蓝在打听：你到底值不值得信。」",
]

var _hook_index: int = 0


func _ready() -> void:
	start_button.pressed.connect(_on_start_pressed)
	quit_button.pressed.connect(_on_quit_pressed)
	start_button.grab_focus()
	AudioManager.play_menu_bgm()
	_play_intro()
	_start_hook_rotation()


## 入场动效：标题下沉淡入 → 卖点标签依次弹出
func _play_intro() -> void:
	title_box.modulate.a = 0.0
	title_box.position.y -= 24
	var tw := create_tween().set_parallel(true)
	tw.tween_property(title_box, "modulate:a", 1.0, 0.7).set_trans(Tween.TRANS_SINE)
	tw.tween_property(title_box, "position:y", title_box.position.y + 24, 0.8) \
		.set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)

	for i in tag_row.get_child_count():
		var tag := tag_row.get_child(i) as Control
		if tag == null:
			continue
		tag.modulate.a = 0.0
		tag.scale = Vector2(0.8, 0.8)
		tag.pivot_offset = tag.size * 0.5
		var t := create_tween().set_parallel(true)
		t.tween_property(tag, "modulate:a", 1.0, 0.35).set_delay(0.5 + i * 0.13)
		t.tween_property(tag, "scale", Vector2.ONE, 0.4) \
			.set_delay(0.5 + i * 0.13).set_trans(Tween.TRANS_BACK).set_ease(Tween.EASE_OUT)


## 悬念文案轮播：每 3.4 秒淡出换句再淡入
func _start_hook_rotation() -> void:
	hook_label.text = HOOKS[0]
	while is_inside_tree():
		await get_tree().create_timer(3.4).timeout
		if not is_inside_tree():
			return
		_hook_index = (_hook_index + 1) % HOOKS.size()
		var tw := create_tween()
		tw.tween_property(hook_label, "modulate:a", 0.0, 0.35)
		await tw.finished
		if not is_inside_tree():
			return
		hook_label.text = HOOKS[_hook_index]
		create_tween().tween_property(hook_label, "modulate:a", 1.0, 0.45)


func _on_start_pressed() -> void:
	AudioManager.play_game_bgm()
	get_tree().change_scene_to_file("res://scenes/main.tscn")


func _on_quit_pressed() -> void:
	get_tree().quit()
