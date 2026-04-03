from app import create_app, db
from app.models import User, Category, Website, InvitationCode
import urllib3

# 禁用不安全请求警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 创建应用实例，设置SQLite支持多线程
app = create_app()

@app.shell_context_processor
def make_shell_context():
    return {
        'db': db, 
        'User': User, 
        'Category': Category, 
        'Website': Website, 
        'InvitationCode': InvitationCode
    }

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0') 
