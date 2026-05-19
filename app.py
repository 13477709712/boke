import re

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
import markdown
import markupsafe
from datetime import datetime
import os
import uuid

from models import db, User, Article, Category

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///blog.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024

db.init_app(app)

with app.app_context():
    db.create_all()
    from sqlalchemy import inspect
    insp = inspect(db.engine)
    if 'category' not in insp.get_table_names():
        db.create_all()
    cols = [c['name'] for c in insp.get_columns('article')]
    if 'category_id' not in cols:
        db.session.execute(
            'ALTER TABLE article ADD COLUMN category_id INTEGER '
            'REFERENCES category(id)'
        )
    if 'views' not in cols:
        db.session.execute(
            'ALTER TABLE article ADD COLUMN views INTEGER DEFAULT 0'
        )
    db.session.commit()

login_manager = LoginManager(app)
login_manager.login_view = 'login'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    category_id = request.args.get('category', type=int)

    query = Article.query
    if category_id:
        query = query.filter_by(category_id=category_id)

    paginator = query.order_by(Article.created_at.desc()).paginate(page=page, per_page=10, error_out=False)
    articles = paginator.items
    categories = Category.query.all()

    md_parser = markdown.Markdown(extensions=['fenced_code', 'tables'])
    for article in articles:
        html = md_parser.convert(article.content)
        text = re.sub(r'<[^>]+>', '', html).replace('&nbsp;', ' ').strip()
        article.excerpt = text[:200]

    return render_template('index.html', articles=articles, paginator=paginator, categories=categories, current_category=category_id)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if not username or not password:
            flash('用户名和密码不能为空', 'error')
            return render_template('register.html')

        if password != confirm:
            flash('两次密码不一致', 'error')
            return render_template('register.html')

        if User.query.filter_by(username=username).first():
            flash('用户名已存在', 'error')
            return render_template('register.html')

        user = User(username=username, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        flash('注册成功，请登录', 'success')
        return redirect(url_for('login'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('index'))
        flash('用户名或密码错误', 'error')

    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.route('/search')
def search():
    q = request.args.get('q', '').strip()
    articles = []
    if q:
        articles = Article.query.filter(
            db.or_(Article.title.contains(q), Article.content.contains(q))
        ).order_by(Article.created_at.desc()).all()
    return render_template('search.html', articles=articles, query=q)


@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404


@app.errorhandler(500)
def server_error(e):
    return render_template('500.html'), 500


@app.route('/new', methods=['GET', 'POST'])
@login_required
def new_article():
    categories = Category.query.all()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        category_id = request.form.get('category_id', type=int) or None

        if not title or not content:
            flash('标题和内容不能为空', 'error')
            return render_template('new.html', categories=categories)

        article = Article(title=title, content=content, user_id=current_user.id, category_id=category_id)
        db.session.add(article)
        db.session.commit()
        flash('文章发布成功', 'success')
        return redirect(url_for('article_detail', article_id=article.id))

    return render_template('new.html', categories=categories)


@app.route('/article/<int:article_id>')
def article_detail(article_id):
    article = Article.query.get_or_404(article_id)
    article.views += 1
    db.session.commit()
    md = markdown.Markdown(extensions=['fenced_code', 'tables', 'codehilite', 'toc'])
    article.html_content = markupsafe.Markup(md.convert(article.content))
    return render_template('article.html', article=article)


@app.route('/upload_image', methods=['POST'])
@login_required
def upload_image():
    file = request.files.get('editormd-image-file')
    if not file:
        return jsonify({'success': 0, 'message': '没有上传文件'})

    filename = file.filename or ''
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ['jpg', 'jpeg', 'gif', 'png', 'bmp', 'webp']:
        return jsonify({'success': 0, 'message': '不支持的图片格式'})

    upload_dir = os.path.join(app.static_folder, 'uploads')
    os.makedirs(upload_dir, exist_ok=True)

    new_filename = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(upload_dir, new_filename))

    url = url_for('static', filename=f'uploads/{new_filename}')
    return jsonify({'success': 1, 'message': '上传成功', 'url': url})


@app.route('/article/<int:article_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_article(article_id):
    article = Article.query.get_or_404(article_id)
    if article.user_id != current_user.id:
        flash('不能编辑别人的文章', 'error')
        return redirect(url_for('index'))

    categories = Category.query.all()
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        category_id = request.form.get('category_id', type=int) or None

        if not title or not content:
            flash('标题和内容不能为空', 'error')
            return render_template('edit.html', article=article, categories=categories)

        article.title = title
        article.content = content
        article.category_id = category_id
        article.updated_at = datetime.utcnow()
        db.session.commit()
        flash('文章已更新', 'success')
        return redirect(url_for('article_detail', article_id=article.id))

    return render_template('edit.html', article=article, categories=categories)


@app.route('/article/<int:article_id>/delete', methods=['POST'])
@login_required
def delete_article(article_id):
    article = Article.query.get_or_404(article_id)
    if article.user_id != current_user.id:
        flash('不能删除别人的文章', 'error')
        return redirect(url_for('index'))

    db.session.delete(article)
    db.session.commit()
    flash('文章已删除', 'success')
    return redirect(url_for('index'))


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)
