extends Control
## 主界面（标题屏）。启动主场景 → 开始游戏切到世界场景；退出关闭游戏。

@onready var start_button: Button = %StartButton
@onready var quit_button: Button = %QuitButton


func _ready() -> void:
	start_button.pressed.connect(_on_start_pressed)
	quit_button.pressed.connect(_on_quit_pressed)
	start_button.grab_focus()
	AudioManager.play_menu_bgm()


func _on_start_pressed() -> void:
	AudioManager.play_game_bgm()
	get_tree().change_scene_to_file("res://scenes/main.tscn")


func _on_quit_pressed() -> void:
	get_tree().quit()
