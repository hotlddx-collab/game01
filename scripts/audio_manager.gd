extends Node
## 全局背景音乐管理器（autoload）。
##
## 用法：
##   AudioManager.play_menu_bgm()     # 主界面音乐
##   AudioManager.play_game_bgm()      # 游戏内音乐
##   AudioManager.play_bgm(path)       # 播放任意 BGM（自动循环 + 交叉淡入淡出）
##   AudioManager.stop_bgm()           # 淡出停止
##
## 跨场景常驻（autoload），切场景时若目标 BGM 与当前不同则平滑切换。

const MENU_BGM := "res://res/ninja_adventure/Audio/Musics/38 - Intro.ogg"
const GAME_BGM := "res://res/ninja_adventure/Audio/Musics/33 - Calm Village.ogg"

@export var bgm_volume_db: float = -8.0   # BGM 目标音量
@export var fade_time: float = 0.8         # 淡入/淡出时长（秒）

var _player: AudioStreamPlayer
var _current_path: String = ""
var _fade_tween: Tween = null


func _ready() -> void:
	process_mode = Node.PROCESS_MODE_ALWAYS  # 暂停时音乐继续
	_player = AudioStreamPlayer.new()
	_player.bus = "Master"
	_player.volume_db = bgm_volume_db
	add_child(_player)


func play_menu_bgm() -> void:
	play_bgm(MENU_BGM)


func play_game_bgm() -> void:
	play_bgm(GAME_BGM)


## 播放指定 BGM。若已在播同一首则忽略；否则淡出旧的、淡入新的。
func play_bgm(path: String) -> void:
	if path == "" or path == _current_path:
		return
	_current_path = path
	var stream := load(path)
	if stream == null:
		push_warning("[AudioManager] 加载失败：%s" % path)
		return
	# ogg / mp3 运行时强制循环
	if stream is AudioStreamOggVorbis or stream is AudioStreamMP3:
		stream.loop = true

	if _fade_tween and _fade_tween.is_valid():
		_fade_tween.kill()

	if _player.playing:
		# 先淡出旧曲，再换流淡入
		_fade_tween = create_tween()
		_fade_tween.tween_property(_player, "volume_db", -40.0, fade_time * 0.5)
		_fade_tween.tween_callback(func():
			_player.stream = stream
			_player.play()
		)
		_fade_tween.tween_property(_player, "volume_db", bgm_volume_db, fade_time)
	else:
		_player.stream = stream
		_player.volume_db = -40.0
		_player.play()
		_fade_tween = create_tween()
		_fade_tween.tween_property(_player, "volume_db", bgm_volume_db, fade_time)


func stop_bgm() -> void:
	_current_path = ""
	if not _player.playing:
		return
	if _fade_tween and _fade_tween.is_valid():
		_fade_tween.kill()
	_fade_tween = create_tween()
	_fade_tween.tween_property(_player, "volume_db", -40.0, fade_time)
	_fade_tween.tween_callback(_player.stop)
