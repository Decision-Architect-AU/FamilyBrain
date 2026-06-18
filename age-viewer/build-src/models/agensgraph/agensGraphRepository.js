"use strict";

var _interopRequireDefault = require("@babel/runtime/helpers/interopRequireDefault");

var _regenerator = _interopRequireDefault(require("@babel/runtime/regenerator"));

var _asyncToGenerator2 = _interopRequireDefault(require("@babel/runtime/helpers/asyncToGenerator"));

var _classCallCheck2 = _interopRequireDefault(require("@babel/runtime/helpers/classCallCheck"));

var _createClass2 = _interopRequireDefault(require("@babel/runtime/helpers/createClass"));

var _Flavors = _interopRequireDefault(require("../../config/Flavors"));

var _Pg = _interopRequireDefault(require("../../config/Pg"));

var _pg = _interopRequireDefault(require("pg"));

var _pgTypes = _interopRequireDefault(require("pg-types"));

var _AGEParser = require("../../tools/AGEParser");

/*
 * Copyright 2020 Bitnine Co., Ltd.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
require('@bitnine-oss/ag-driver');

var AgensGraphRepository = /*#__PURE__*/function () {
  function AgensGraphRepository() {
    var _ref = arguments.length > 0 && arguments[0] !== undefined ? arguments[0] : {},
        host = _ref.host,
        port = _ref.port,
        database = _ref.database,
        graph = _ref.graph,
        user = _ref.user,
        password = _ref.password,
        flavor = _ref.flavor;

    (0, _classCallCheck2["default"])(this, AgensGraphRepository);

    if (!flavor) {
      throw new Error('Flavor is required.');
    }

    this._host = host;
    this._port = port;
    this._database = database;
    this._graph = graph;
    this._user = user;
    this._password = password;
    this.flavor = flavor;
  }

  (0, _createClass2["default"])(AgensGraphRepository, [{
    key: "execute",
    value: // Execute cypher query with params
    function () {
      var _execute = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee(query) {
        var params,
            client,
            result,
            _args = arguments;
        return _regenerator["default"].wrap(function _callee$(_context) {
          while (1) {
            switch (_context.prev = _context.next) {
              case 0:
                params = _args.length > 1 && _args[1] !== undefined ? _args[1] : [];
                _context.next = 3;
                return this.getConnection();

              case 3:
                client = _context.sent;
                result = null;
                _context.prev = 5;
                _context.next = 8;
                return client.query(query, params);

              case 8:
                result = _context.sent;
                _context.next = 14;
                break;

              case 11:
                _context.prev = 11;
                _context.t0 = _context["catch"](5);
                throw _context.t0;

              case 14:
                _context.prev = 14;
                client.release();
                return _context.finish(14);

              case 17:
                return _context.abrupt("return", result);

              case 18:
              case "end":
                return _context.stop();
            }
          }
        }, _callee, this, [[5, 11, 14, 17]]);
      }));

      function execute(_x) {
        return _execute.apply(this, arguments);
      }

      return execute;
    }()
    /**
     * Get connectionInfo
     */

  }, {
    key: "getConnection",
    value: function () {
      var _getConnection = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee2() {
        var client;
        return _regenerator["default"].wrap(function _callee2$(_context2) {
          while (1) {
            switch (_context2.prev = _context2.next) {
              case 0:
                if (!this._pool) {
                  this._pool = AgensGraphRepository.newConnectionPool(this.getPoolConnectionInfo());
                }

                _context2.next = 3;
                return this._pool.connect();

              case 3:
                client = _context2.sent;

                if (!(this.flavor === 'AGE')) {
                  _context2.next = 9;
                  break;
                }

                _context2.next = 7;
                return (0, _AGEParser.setAGETypes)(client, _pgTypes["default"]);

              case 7:
                _context2.next = 11;
                break;

              case 9:
                _context2.next = 11;
                return client.query("set graph_path = ".concat(this._graph));

              case 11:
                return _context2.abrupt("return", client);

              case 12:
              case "end":
                return _context2.stop();
            }
          }
        }, _callee2, this);
      }));

      function getConnection() {
        return _getConnection.apply(this, arguments);
      }

      return getConnection;
    }()
    /**
     * Release connection
     */

  }, {
    key: "releaseConnection",
    value: function () {
      var _releaseConnection = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee3() {
        return _regenerator["default"].wrap(function _callee3$(_context3) {
          while (1) {
            switch (_context3.prev = _context3.next) {
              case 0:
                _context3.prev = 0;
                _context3.next = 3;
                return this._pool.end();

              case 3:
                return _context3.abrupt("return", true);

              case 6:
                _context3.prev = 6;
                _context3.t0 = _context3["catch"](0);
                throw _context3.t0;

              case 9:
              case "end":
                return _context3.stop();
            }
          }
        }, _callee3, this, [[0, 6]]);
      }));

      function releaseConnection() {
        return _releaseConnection.apply(this, arguments);
      }

      return releaseConnection;
    }()
    /**
     * Get connection pool information
     */

  }, {
    key: "getPoolConnectionInfo",
    value: function getPoolConnectionInfo() {
      if (!this._host || !this._port || !this._database) {
        return null;
      }

      return {
        host: this._host,
        port: this._port,
        database: this._database,
        user: this._user,
        password: this._password,
        max: _Pg["default"].max,
        idleTimeoutMillis: _Pg["default"].idleTimeoutMillis,
        connectionTimeoutMillis: _Pg["default"].connectionTimeoutMillis
      };
    }
    /**
     * Get connection info
     */

  }, {
    key: "getConnectionInfo",
    value: function getConnectionInfo() {
      if (!this._host || !this._port || !this._database) {
        throw new Error("Not connected");
      }

      return {
        host: this._host,
        port: this._port,
        database: this._database,
        user: this._user,
        password: this._password,
        graph: this._graph,
        flavor: this.flavor
      };
    }
  }], [{
    key: "getConnection",
    value: function () {
      var _getConnection2 = (0, _asyncToGenerator2["default"])( /*#__PURE__*/_regenerator["default"].mark(function _callee4() {
        var _ref2,
            host,
            port,
            database,
            graph,
            user,
            password,
            flavor,
            closeConnection,
            client,
            _args4 = arguments;

        return _regenerator["default"].wrap(function _callee4$(_context4) {
          while (1) {
            switch (_context4.prev = _context4.next) {
              case 0:
                _ref2 = _args4.length > 0 && _args4[0] !== undefined ? _args4[0] : {}, host = _ref2.host, port = _ref2.port, database = _ref2.database, graph = _ref2.graph, user = _ref2.user, password = _ref2.password, flavor = _ref2.flavor;
                closeConnection = _args4.length > 1 && _args4[1] !== undefined ? _args4[1] : true;
                client = new _pg["default"].Client({
                  user: user,
                  password: password,
                  host: host,
                  database: database,
                  port: port
                });
                client.connect();

                if (!(flavor === _Flavors["default"].AGE)) {
                  _context4.next = 9;
                  break;
                }

                _context4.next = 7;
                return (0, _AGEParser.setAGETypes)(client, _pgTypes["default"]);

              case 7:
                _context4.next = 15;
                break;

              case 9:
                if (!(flavor === _Flavors["default"].AGENS)) {
                  _context4.next = 14;
                  break;
                }

                _context4.next = 12;
                return client.query("set graph_path = ".concat(graph));

              case 12:
                _context4.next = 15;
                break;

              case 14:
                throw new Error("Unknown flavor ".concat(flavor));

              case 15:
                if (!(closeConnection === true)) {
                  _context4.next = 18;
                  break;
                }

                _context4.next = 18;
                return client.end();

              case 18:
                return _context4.abrupt("return", client);

              case 19:
              case "end":
                return _context4.stop();
            }
          }
        }, _callee4);
      }));

      function getConnection() {
        return _getConnection2.apply(this, arguments);
      }

      return getConnection;
    }()
  }, {
    key: "newConnectionPool",
    value: function newConnectionPool(poolConnectionConfig) {
      return new _pg["default"].Pool(poolConnectionConfig);
    }
  }]);
  return AgensGraphRepository;
}();

module.exports = AgensGraphRepository;
//# sourceMappingURL=agensGraphRepository.js.map