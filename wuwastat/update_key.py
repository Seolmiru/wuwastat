"""프로그램 업데이트 서명을 확인하는 공개 키 (Ed25519 32바이트, base64).

비밀 키는 저장소에 없고 배포하는 사람의 PC에만 있다.
- 키 만들기: tools/gen_update_key.py (이 파일의 PUBLIC_KEY_B64를 자동으로 바꾼다)
- 릴리즈 서명: tools/sign_release.py
공개 키를 바꾸면 이전 버전 앱은 새 키로 서명한 업데이트를 거부한다 (수동으로 한 번 다시 받아야 함).
"""

PUBLIC_KEY_B64 = "j+e4PoEyuf8aq361s/T3xdVHKCN1KIF9+fXVRzd83wE="
