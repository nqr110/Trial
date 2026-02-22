# -*- coding: utf-8 -*-
from flask import Blueprint, request, redirect, url_for, session, render_template, current_app

auth_bp = Blueprint("auth", __name__)

DEFAULT_USER = "root"
DEFAULT_PASSWORD = "itzx"


def _auth_debug(msg):
    log = current_app.config.get("DEBUG_LOG") if current_app else None
    if callable(log):
        log(msg)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    username = (request.form.get("username") or "").strip()
    password = (request.form.get("password") or "").strip()
    if username == DEFAULT_USER and password == DEFAULT_PASSWORD:
        session["logged_in"] = True
        session["username"] = username
        _auth_debug("登录成功: user=%s" % username)
        return redirect(url_for("chat.index"))
    _auth_debug("登录失败: user=%s (用户名或密码错误)" % username)
    return render_template("login.html", error="用户名或密码错误"), 401


@auth_bp.route("/logout", methods=["GET", "POST"])
def logout():
    user = session.get("username", "")
    session.pop("logged_in", None)
    session.pop("username", None)
    _auth_debug("登出: user=%s" % user)
    return redirect(url_for("auth.login"))
