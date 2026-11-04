import subprocess
import os
import sys

# 获取项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def run_script(args, env=None, cwd=None):
    """运行 extract_mp3.sh 并返回结果"""
    script_path = os.path.join(PROJECT_ROOT, 'extract_mp3.sh')
    # 使用 mock 的 PATH
    mock_dir = os.path.join(PROJECT_ROOT, 'tests', 'mocks')
    new_env = os.environ.copy()
    new_env['PATH'] = mock_dir + os.pathsep + new_env.get('PATH', '')
    if env:
        new_env.update(env)

    result = subprocess.run(
        ['bash', script_path] + args,
        capture_output=True,
        text=True,
        env=new_env,
        cwd=cwd or PROJECT_ROOT
    )
    return result


class TestMultiFiles:
    def test_multi_files_sequential(self, tmp_path):
        """测试多文件顺序处理，验证输出文件存在"""
        inputs = []
        for name in ['a.mp4', 'b.mkv', 'c.mov']:
            f = tmp_path / name
            f.write_text(f'fake video content {name}')
            inputs.append(str(f))

        result = run_script(inputs, cwd=str(tmp_path))

        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("RETURNCODE:", result.returncode)

        assert result.returncode == 0, f"Script failed: {result.stderr}"

        # 检查输出文件
        assert (tmp_path / 'a.mp3').exists()
        assert (tmp_path / 'b.mp3').exists()
        assert (tmp_path / 'c.mp3').exists()

    def test_progress_display(self, tmp_path):
        """测试进度显示格式"""
        inputs = []
        for name in ['a.mp4', 'b.mkv', 'c.mov']:
            f = tmp_path / name
            f.write_text(f'fake video content {name}')
            inputs.append(str(f))

        result = run_script(inputs, cwd=str(tmp_path))

        assert "[1/3 | 33%]" in result.stdout
        assert "[2/3 | 66%]" in result.stdout
        assert "[3/3 | 100%]" in result.stdout

    def test_continue_on_failure(self, tmp_path):
        """测试失败时继续处理后续文件"""
        inputs = []
        for name in ['ok.mp4', 'fail.mp4', 'ok2.mp4']:
            f = tmp_path / name
            f.write_text(f'fake video content {name}')
            inputs.append(str(f))

        # 让包含 fail.mp4 的文件触发 ffmpeg 失败
        result = run_script(inputs, env={'TEST_FFMPEG_FAIL': 'fail.mp4'}, cwd=str(tmp_path))

        # 总体返回 1 因为有失败
        assert result.returncode == 1

        # 但 ok 和 ok2 应该成功
        assert (tmp_path / 'ok.mp3').exists()
        assert (tmp_path / 'ok2.mp3').exists()
        assert not (tmp_path / 'fail.mp3').exists()

        # 统计信息
        assert "2 succeeded, 1 failed (total: 3)" in result.stdout

    def test_auto_output_naming(self, tmp_path):
        """测试自动输出命名 .mp3"""
        f = tmp_path / 'video.mp4'
        f.write_text('fake video')

        result = run_script([str(f)], cwd=str(tmp_path))

        assert result.returncode == 0
        assert (tmp_path / 'video.mp3').exists()

    def test_summary_stats(self, tmp_path):
        """测试最终统计信息"""
        inputs = []
        for name in ['a.mp4']:
            f = tmp_path / name
            f.write_text('fake')
            inputs.append(str(f))

        result = run_script(inputs, cwd=str(tmp_path))

        assert "All tasks completed: 1 succeeded, 0 failed (total: 1)" in result.stdout

    def test_options_with_multi_files(self, tmp_path):
        """测试多文件时选项仍正确传递并显示"""
        inputs = []
        for name in ['a.mp4', 'b.mp4']:
            f = tmp_path / name
            f.write_text('fake')
            inputs.append(str(f))

        result = run_script(['--bitrate', '128k'] + inputs, cwd=str(tmp_path))

        assert result.returncode == 0
        assert "Bitrate:      128k" in result.stdout

    def test_missing_input_file_continue(self, tmp_path):
        """测试输入文件不存在时跳过并继续"""
        real_file = tmp_path / 'exists.mp4'
        real_file.write_text('fake')
        missing_file = tmp_path / 'missing.mp4'

        result = run_script([str(missing_file), str(real_file)], cwd=str(tmp_path))

        # 有一个文件失败，总体返回 1
        assert result.returncode == 1
        assert (tmp_path / 'exists.mp3').exists()
        assert "Error: input file does not exist" in result.stdout

    def test_copy_mp3_branch(self, tmp_path):
        """测试检测到 mp3 编码时走 copy 分支"""
        f = tmp_path / 'audio.mp4'
        f.write_text('fake mp3 audio')

        result = run_script([str(f)], env={'TEST_FFPROBE_CODEC': 'mp3'}, cwd=str(tmp_path))

        assert result.returncode == 0
        assert (tmp_path / 'audio.mp3').exists()
        assert "copying audio stream without re-encoding" in result.stdout


class TestScriptContent:
    """通过静态分析检查脚本内容"""

    def test_ffmpeg_loglevel_warning(self):
        """检查 ffmpeg 命令包含 -loglevel warning"""
        script_path = os.path.join(PROJECT_ROOT, 'extract_mp3.sh')
        with open(script_path, 'r') as f:
            content = f.read()

        assert '-loglevel warning' in content
        # 确保有两处（copy 和 re-encode 两个分支）
        assert content.count('-loglevel warning') >= 2

    def test_no_output_file_argument(self):
        """检查帮助文档不再接受 OUTPUT_FILE 参数"""
        script_path = os.path.join(PROJECT_ROOT, 'extract_mp3.sh')
        with open(script_path, 'r') as f:
            content = f.read()

        assert 'INPUT_FILE [INPUT_FILE ...]' in content


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
